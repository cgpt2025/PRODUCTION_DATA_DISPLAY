from flask import Flask, render_template, request, redirect, url_for, session
import pyodbc
from datetime import date, datetime, time as dtime

app = Flask(__name__)
app.secret_key = "mtr_prod_s3cr3t_key_2026"   # change in production

# ========== AUTH ==========

APP_USERNAME = "admin"
APP_PASSWORD = "Admin#4545"

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

DEFAULT_PLANT = "GPIL-2"   # ← Change here to set a different default plant

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


def get_hourly_for_project(server, proj_db, proj_id, proj_code, sa_pwd,
                           selected_date, hour_from, hour_to):
    """
    hour_from / hour_to are integers (0–23) representing the full hours to include.
    Filter: HourOfDay >= hour_from AND HourOfDay <= hour_to
    """
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
          AND DATEPART(HOUR, st.STDt) >= ?
          AND DATEPART(HOUR, st.STDt) <= ?
        GROUP BY u.UserName, DATEPART(HOUR, st.STDt), st.ProjId
        ORDER BY u.UserName, st.ProjId, DATEPART(HOUR, st.STDt)
    """, (proj_code, date_str, date_str, proj_id, hour_from, hour_to))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_plant(plant_key, selected_date, hour_from, hour_to):
    cfg      = PLANTS[plant_key]
    projects = get_projects_for_plant(plant_key, selected_date)
    sa_pwd   = SA_PWD_RCP if plant_key == "RCP" else SA_PWD_DEFAULT
    all_rows = []

    for tab_id, proj_code, db_name in projects:
        for r in get_hourly_for_project(
            cfg["server"], db_name.strip(), tab_id,
            proj_code.strip(), sa_pwd,
            selected_date, hour_from, hour_to
        ):
            all_rows.append({
                "ProjCode": getattr(r, "ProjCode",  r[0]),
                "Line":     getattr(r, "Line",      r[1]),
                "Hour":     getattr(r, "HourOfDay", r[2]),
                "ProdQty":  getattr(r, "ProdQty",   r[3]),
            })

    all_rows.sort(key=lambda r: (r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows


# ════════════════════════════════════════════════════════════
# ALL PLANTS AGGREGATION
# To disable "All Plants" entirely, comment out this function
# AND the "ALL" block inside the index() route below.
# ════════════════════════════════════════════════════════════
def get_hourly_all_plants(selected_date, hour_from, hour_to):
    all_rows = []
    for plant_key in PLANTS:
        try:
            plant_rows = get_hourly_for_plant(plant_key, selected_date, hour_from, hour_to)
            for r in plant_rows:
                r["Plant"] = plant_key
            all_rows.extend(plant_rows)
        except Exception as exc:
            app.logger.warning("Could not fetch data for %s: %s", plant_key, exc)

    all_rows.sort(key=lambda r: (r["Plant"], r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows
# ── END ALL PLANTS AGGREGATION ───────────────────────────────


def hour_slot_label(h):
    return f"{h:02d}:00 – {(h + 1) % 24:02d}:00"


def parse_hour(time_str, default_hour):
    """Parse 'HH:MM' string, return the hour integer."""
    try:
        return int(time_str.split(":")[0])
    except (ValueError, AttributeError, IndexError):
        return default_hour


# ========== AUTH ROUTES ==========

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == APP_USERNAME and password == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        return render_template("login.html",
                               error="Invalid username or password.",
                               username=username)
    return render_template("login.html", error=None, username="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ========== MAIN ROUTE ==========

@app.route("/")
def index():
    # Auth guard
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    # Plant — default GPIL-2
    plant = request.args.get("plant", DEFAULT_PLANT)

    # Date — default today
    date_str = request.args.get("date", "")
    try:
        selected_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        selected_date = date.today()

    # Time range — default 00:00 to 23:00
    raw_from = request.args.get("time_from", "00:00")
    raw_to   = request.args.get("time_to",   "23:00")
    hour_from = parse_hour(raw_from, 0)
    hour_to   = parse_hour(raw_to,  23)
    if hour_from > hour_to:
        hour_from, hour_to = 0, 23   # reset if invalid range

    # ── ALL PLANTS block ─────────────────────────────────────
    # Comment out from here …
    if plant == "ALL":
        rows = get_hourly_all_plants(selected_date, hour_from, hour_to)
    # … to here to remove "All Plants" support.
    # ── END ALL PLANTS block ─────────────────────────────────
    else:
        if plant not in PLANTS:
            plant = DEFAULT_PLANT
        rows = get_hourly_for_plant(plant, selected_date, hour_from, hour_to)
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
        time_from=f"{hour_from:02d}:00",
        time_to=f"{hour_to:02d}:00",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=True)
