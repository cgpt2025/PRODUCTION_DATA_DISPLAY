from flask import Flask, render_template, request
import pyodbc
from datetime import date

app = Flask(__name__)

# ========== ODBC DRIVER ==========

ODBC_DRIVER = "{SQL Server}"

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

PLANTS = {
    "GPIL-1":   {"server": "10.133.1.22",   "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
    "GPIL-2":   {"server": "10.133.100.21",  "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
    "GIL":      {"server": "10.134.1.21",    "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
    "RCP":      {"server": "10.141.61.40",   "db": "ProjectConfiguration", "user": "sa", "pwd": "genus_PROD"},
    "GPIL1200": {"server": "10.161.1.22",    "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
}

SA_PWD_DEFAULT = "pass@123"
SA_PWD_RCP     = "genus_PROD"

# ========== CORE QUERIES ==========

def get_projects_for_plant(plant_key, selected_date):
    cfg      = PLANTS[plant_key]
    conn     = make_conn(cfg["server"], cfg["db"], cfg["user"], cfg["pwd"])
    cur      = conn.cursor()
    date_str = selected_date.strftime("%Y-%m-%d")
    cur.execute("""
        SELECT a.TabId, a.ProjCode, b.DbName
        FROM TabProjDesc a
        INNER JOIN TabUtilityMaster b ON a.UtilityId = b.TabId
        WHERE a.LastAccessed >= CONVERT(date, ?)
          AND a.LastAccessed <  DATEADD(day, 1, CONVERT(date, ?))
    """, (date_str, date_str))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_project(server, proj_db, proj_id, proj_code, sa_pwd, selected_date):
    conn     = make_conn(server, proj_db, "sa", sa_pwd)
    cur      = conn.cursor()
    date_str = selected_date.strftime("%Y-%m-%d")
    cur.execute("""
        SELECT
            ? AS ProjCode,
            u.UserName AS Line,
            DATEPART(HOUR, st.STDt) AS HourOfDay,
            COUNT(st.ProjID) AS ProdQty
        FROM TabSTInfo st
        INNER JOIN ProjectConfiguration.dbo.TabUserMaster u
            ON st.UserId = u.TabId
        WHERE st.STDt >= CONVERT(date, ?)
          AND st.STDt <  DATEADD(day, 1, CONVERT(date, ?))
          AND st.ProjId = ?
        GROUP BY u.UserName, DATEPART(HOUR, st.STDt), st.ProjId
        ORDER BY u.UserName, st.ProjId, DATEPART(HOUR, st.STDt)
    """, (proj_code, date_str, date_str, proj_id))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_plant(plant_key, selected_date):
    """Fetch hourly rows for a single plant. Each row dict has no 'Plant' key."""
    cfg      = PLANTS[plant_key]
    projects = get_projects_for_plant(plant_key, selected_date)
    sa_pwd   = SA_PWD_RCP if plant_key == "RCP" else SA_PWD_DEFAULT
    all_rows = []

    for tab_id, proj_code, db_name in projects:
        for r in get_hourly_for_project(
            cfg["server"], db_name.strip(), tab_id,
            proj_code.strip(), sa_pwd, selected_date
        ):
            all_rows.append({
                "ProjCode": getattr(r, "ProjCode", r[0]),
                "Line":     getattr(r, "Line",     r[1]),
                "Hour":     getattr(r, "HourOfDay", r[2]),
                "ProdQty":  getattr(r, "ProdQty",  r[3]),
            })

    all_rows.sort(key=lambda r: (r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows


# ════════════════════════════════════════════════════════════
# ALL PLANTS AGGREGATION
# To disable "All Plants" entirely, comment out this function
# AND the "ALL" block inside the index() route below.
# ════════════════════════════════════════════════════════════
def get_hourly_all_plants(selected_date):
    """Fetch hourly rows from every plant. Each row dict includes a 'Plant' key."""
    all_rows = []
    for plant_key in PLANTS:
        try:
            plant_rows = get_hourly_for_plant(plant_key, selected_date)
            for r in plant_rows:
                r["Plant"] = plant_key   # tag which plant this row belongs to
            all_rows.extend(plant_rows)
        except Exception as exc:
            # If one plant is unreachable, log and continue
            app.logger.warning("Could not fetch data for %s: %s", plant_key, exc)

    all_rows.sort(key=lambda r: (r["Plant"], r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows
# ── END ALL PLANTS AGGREGATION ───────────────────────────────


def hour_slot_label(h):
    return f"{h:02d}:00 - {(h + 1) % 24:02d}:00"


# ========== FLASK ROUTE ==========

@app.route("/")
def index():
    plant = request.args.get("plant", "GPIL-1")

    # Date selector — default to today
    date_str = request.args.get("date", "")
    try:
        selected_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        selected_date = date.today()

    # ── ALL PLANTS block ─────────────────────────────────────
    # Comment out from here …
    if plant == "ALL":
        rows = get_hourly_all_plants(selected_date)
    # … to here to remove "All Plants" support.
    # ── END ALL PLANTS block ─────────────────────────────────
    else:
        if plant not in PLANTS:
            plant = "GPIL-1"
        rows = get_hourly_for_plant(plant, selected_date)
        # Single-plant rows have no "Plant" key; add a blank one for template safety
        for r in rows:
            r.setdefault("Plant", "")

    for r in rows:
        r["Slot"] = hour_slot_label(r["Hour"])

    return render_template(
        "production_hourly_single.html",
        plant=plant,
        rows=rows,
        plant_names=list(PLANTS.keys()),
        today=selected_date.strftime("%d-%b-%Y"),
        selected_date=selected_date.isoformat(),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=True)
