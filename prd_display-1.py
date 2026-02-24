from flask import Flask, render_template, request
import pyodbc
from datetime import date

app = Flask(__name__)

# ========== ODBC DRIVER ==========

ODBC_DRIVER = "{SQL Server}"  # same driver you used in test_conn.py

def make_conn(server, database, user, pwd):
    conn_str = (
        f"DRIVER={ODBC_DRIVER};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};PWD={pwd};"
        "Connect Timeout=5"
    )
    return pyodbc.connect(conn_str, timeout=5)

# ========== CREDENTIALS PER PLANT ==========

# Main ProjectConfiguration DB (plant-level)
GPIL1_SERVER      = "10.133.1.22"
GPIL1_DB          = "ProjectConfiguration"
GPIL1_USER        = "sa"
GPIL1_PWD         = "pass@123"

GPIL2_SERVER      = "10.133.100.21"
GPIL2_DB          = "ProjectConfiguration"
GPIL2_USER        = "sa"
GPIL2_PWD         = "pass@123"

GIL_SERVER        = "10.134.1.21"
GIL_DB            = "ProjectConfiguration"
GIL_USER          = "sa"
GIL_PWD           = "pass@123"

RCP_SERVER        = "10.141.61.40"
RCP_DB            = "ProjectConfiguration"
RCP_USER          = "sa"
RCP_PWD           = "genus_PROD"

GPIL1200_SERVER   = "10.161.1.22"
GPIL1200_DB       = "ProjectConfiguration"
GPIL1200_USER     = "sa"
GPIL1200_PWD      = "pass@123"

# Map plants to their config
PLANTS = {
    "GPIL-1":   {"server": GPIL1_SERVER,    "db": GPIL1_DB,    "user": GPIL1_USER,    "pwd": GPIL1_PWD},
    "GPIL-2":   {"server": GPIL2_SERVER,    "db": GPIL2_DB,    "user": GPIL2_USER,    "pwd": GPIL2_PWD},
    "GIL":      {"server": GIL_SERVER,      "db": GIL_DB,      "user": GIL_USER,      "pwd": GIL_PWD},
    "RCP":      {"server": RCP_SERVER,      "db": RCP_DB,      "user": RCP_USER,      "pwd": RCP_PWD},
    "GPIL1200": {"server": GPIL1200_SERVER, "db": GPIL1200_DB, "user": GPIL1200_USER, "pwd": GPIL1200_PWD},
}

# Per-project sa passwords (from Home.cs)
SA_PWD_DEFAULT = "pass@123"
SA_PWD_RCP     = "genus_PROD"  # used when connecting to RCP project DBs

# ========== CORE QUERIES ==========

def get_projects_for_plant(plant_key):
    cfg = PLANTS[plant_key]
    conn = make_conn(cfg["server"], cfg["db"], cfg["user"], cfg["pwd"])
    cur = conn.cursor()
    cur.execute("""
        SELECT a.TabId, a.ProjCode, b.DbName
        FROM TabProjDesc a
        INNER JOIN TabUtilityMaster b ON a.UtilityId = b.TabId
        WHERE a.LastAccessed >= CONVERT(varchar, GETDATE(), 110)
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_project(server, proj_db, proj_id, proj_code, sa_pwd):
    conn = make_conn(server, proj_db, "sa", sa_pwd)
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            ? AS ProjCode,
            u.UserName AS Line,
            DATEPART(HOUR, st.STDt) AS HourOfDay,
            COUNT(st.ProjID) AS ProdQty
        FROM TabSTInfo st
        INNER JOIN ProjectConfiguration.dbo.TabUserMaster u
            ON st.UserId = u.TabId
        WHERE st.STDt >= CONVERT(date, GETDATE())
          AND st.STDt <  DATEADD(day, 1, CONVERT(date, GETDATE()))
          AND st.ProjId = ?
        GROUP BY u.UserName, DATEPART(HOUR, st.STDt), st.ProjId
        ORDER BY u.UserName, st.ProjId, DATEPART(HOUR, st.STDt)
    """, (proj_code, proj_id))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_plant(plant_key):
    cfg = PLANTS[plant_key]
    projects = get_projects_for_plant(plant_key)
    all_rows = []

    sa_pwd = SA_PWD_RCP if plant_key == "RCP" else SA_PWD_DEFAULT

    for tab_id, proj_code, db_name in projects:
        proj_rows = get_hourly_for_project(
            cfg["server"],
            db_name.strip(),
            tab_id,
            proj_code.strip(),
            sa_pwd
        )
        for r in proj_rows:
            proj = getattr(r, "ProjCode", r[0])
            line = getattr(r, "Line", r[1])
            hour = getattr(r, "HourOfDay", r[2])
            qty  = getattr(r, "ProdQty", r[3])
            all_rows.append({
                "ProjCode": proj,
                "Line": line,
                "Hour": hour,
                "ProdQty": qty,
            })

    # Ensure Line -> ProjCode -> Hour order
    all_rows.sort(key=lambda r: (r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows


def hour_slot_label(h):
    start = f"{h:02d}:00"
    end = f"{(h + 1) % 24:02d}:00"
    return f"{start} - {end}"

# ========== FLASK ROUTE ==========

@app.route("/")
def index():
    # selected plant from dropdown (query string ?plant=GPIL-1), default GPIL-1
    plant = request.args.get("plant", "GPIL-1")
    if plant not in PLANTS:
        plant = "GPIL-1"

    rows = get_hourly_for_plant(plant)

    # enrich rows with slot labels
    for r in rows:
        r["Slot"] = hour_slot_label(r["Hour"])

    today = date.today().strftime("%d-%b-%Y")
    plant_names = list(PLANTS.keys())
    return render_template(
        "production_hourly_single.html",
        plant=plant,
        rows=rows,
        plant_names=plant_names,
        today=today
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=True)
