from flask import Flask, render_template, request, redirect, url_for, session, jsonify


import pyodbc
from datetime import date, datetime, timedelta,  time as dtime

app = Flask(__name__)
app.secret_key = "mtr_prod_s3cr3t_key_2026"  # change in production

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
    "GPIL-2":   {"server": "10.133.100.21", "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
    "GIL":      {"server": "10.134.1.21",   "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
    "RCP":      {"server": "10.141.61.40",  "db": "ProjectConfiguration", "user": "sa", "pwd": "genus_PROD"},
    "GUWAHATI": {"server": "10.161.1.22",   "db": "ProjectConfiguration", "user": "sa", "pwd": "pass@123"},
}

DEFAULT_PLANT = "GPIL-2"  # ← Change here to set a different default plant

SA_PWD_DEFAULT = "pass@123"
SA_PWD_RCP = "genus_PROD"

# ========== CORE QUERIES ==========


def get_projects_for_plant(plant_key, selected_date):
    cfg = PLANTS[plant_key]
    conn = make_conn(cfg["server"], cfg["db"], cfg["user"], cfg["pwd"])
    cur = conn.cursor()
    date_str = selected_date.strftime("%Y-%m-%d")

    cur.execute(
        """
        SELECT a.TabId, a.ProjCode, b.DbName
        FROM TabProjDesc a
        INNER JOIN TabUtilityMaster b ON a.UtilityId = b.TabId
        WHERE a.LastAccessed >= CONVERT(date, ?)
          AND a.LastAccessed < DATEADD(day, 1, CONVERT(date, ?))
        """,
        (date_str, date_str),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_project(
    server,
    proj_db,
    proj_id,
    proj_code,
    sa_pwd,
    selected_date,
    dt_from,
    dt_to,
):
    """
    dt_from / dt_to are full datetime bounds (inclusive lower, exclusive upper).
    We still GROUP BY hour-of-day so the UI remains hourly, but only events
    whose timestamps fall in [dt_from, dt_to) are counted.
    """
    conn = make_conn(server, proj_db, "sa", sa_pwd)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            ? AS ProjCode,
            u.UserName AS Line,
            DATEPART(HOUR, st.STDt) AS HourOfDay,
            COUNT(st.ProjID) AS ProdQty
        FROM TabSTInfo st
        INNER JOIN ProjectConfiguration.dbo.TabUserMaster u
            ON st.UserId = u.TabId
        WHERE st.STDt >= ?
          AND st.STDt < ?
          AND st.ProjId = ?
        GROUP BY u.UserName, DATEPART(HOUR, st.STDt), st.ProjId
        ORDER BY u.UserName, st.ProjId, DATEPART(HOUR, st.STDt)
        """,
        (proj_code, dt_from, dt_to, proj_id),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_hourly_for_plant(plant_key, selected_date, dt_from, dt_to):
    cfg = PLANTS[plant_key]
    projects = get_projects_for_plant(plant_key, selected_date)
    sa_pwd = SA_PWD_RCP if plant_key == "RCP" else SA_PWD_DEFAULT

    all_rows = []
    for tab_id, proj_code, db_name in projects:
        for r in get_hourly_for_project(
            cfg["server"],
            db_name.strip(),
            tab_id,
            proj_code.strip(),
            sa_pwd,
            selected_date,
            dt_from,
            dt_to,
        ):
            all_rows.append(
                {
                    "ProjCode": getattr(r, "ProjCode", r[0]),
                    "Line": getattr(r, "Line", r[1]),
                    "Hour": getattr(r, "HourOfDay", r[2]),
                    "ProdQty": getattr(r, "ProdQty", r[3]),
                }
            )

    all_rows.sort(key=lambda r: (r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows

def get_today_total_for_plant(plant_key, today):
    """
    Returns total meters produced today till now for a single plant.
    """
    cfg = PLANTS[plant_key]
    conn = make_conn(cfg["server"], cfg["db"], cfg["user"], cfg["pwd"])
    cur = conn.cursor()

    # Figure out which projects belong to this plant today
    projects = get_projects_for_plant(plant_key, today)
    if not projects:
        conn.close()
        return 0

    # Build list of project IDs
    proj_ids = [tab_id for (tab_id, proj_code, db_name) in projects]

    # Time range: today 00:00 to now
    today_start = datetime.combine(today, dtime(0, 0))
    now_dt = datetime.now()

    # We assume TabSTInfo is in the respective project DBs; we’ll
    # sum across each project DB one by one (like your hourly logic).
    total = 0
    sa_pwd = SA_PWD_RCP if plant_key == "RCP" else SA_PWD_DEFAULT

    for tab_id, proj_code, db_name in projects:
        proj_conn = make_conn(cfg["server"], db_name.strip(), "sa", sa_pwd)
        proj_cur = proj_conn.cursor()
        proj_cur.execute(
            """
            SELECT COUNT(st.ProjId)
            FROM TabSTInfo st
            WHERE st.STDt >= ?
              AND st.STDt < ?
              AND st.ProjId = ?
            """,
            (today_start, now_dt, tab_id),
        )
        row = proj_cur.fetchone()
        proj_conn.close()
        if row and row[0] is not None:
            total += int(row[0])

    conn.close()
    return total


# ════════════════════════════════════════════════════════════
# ALL PLANTS AGGREGATION
# To disable "All Plants" entirely, comment out this function
# AND the "ALL" block inside the index() route below.
# ════════════════════════════════════════════════════════════


def get_hourly_all_plants(selected_date, dt_from, dt_to):
    all_rows = []
    for plant_key in PLANTS:
        try:
            plant_rows = get_hourly_for_plant(plant_key, selected_date, dt_from, dt_to)
            for r in plant_rows:
                r["Plant"] = plant_key
            all_rows.extend(plant_rows)
        except Exception as exc:
            app.logger.warning("Could not fetch data for %s: %s", plant_key, exc)

    all_rows.sort(key=lambda r: (r["Plant"], r["Line"], r["ProjCode"], r["Hour"]))
    return all_rows


# ── END ALL PLANTS AGGREGATION ───────────────────────────────


def hour_slot_label(h):
    # Keeps the same hourly display
    return f"{h:02d}:00 – {(h + 1) % 24:02d}:00"


def parse_time(time_str, default_time):
    """
    Parse 'HH:MM' string, return a datetime.time.
    If invalid, return default_time.
    """
    try:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return dtime(hour=hour, minute=minute)
    except (ValueError, AttributeError, IndexError):
        return default_time


# ========== AUTH ROUTES ==========


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == APP_USERNAME and password == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))

        return render_template(
            "login.html",
            error="Invalid username or password.",
            username=username,
        )

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

    # Time range — default full day
    raw_from = request.args.get("timefrom", "00:00")
    raw_to = request.args.get("timeto", "23:59")

    t_from = parse_time(raw_from, dtime(0, 0))
    t_to = parse_time(raw_to, dtime(23, 59))

    # Build full datetime range [dt_from, dt_to)
    dt_from = datetime.combine(selected_date, t_from)
    dt_to = datetime.combine(selected_date, t_to)

    # If user accidentally reversed times, normalise to full day
    if dt_from >= dt_to:
        dt_from = datetime.combine(selected_date, dtime(0, 0))
        dt_to = datetime.combine(selected_date, dtime(23, 59))

    # ── ALL PLANTS block ─────────────────────────────────────
    # Comment out from here …
    if plant == "ALL":
        rows = get_hourly_all_plants(selected_date, dt_from, dt_to)
    # … to here to remove "All Plants" support.
    # ── END ALL PLANTS block ─────────────────────────────────
    else:
        if plant not in PLANTS:
            plant = DEFAULT_PLANT
        rows = get_hourly_for_plant(plant, selected_date, dt_from, dt_to)
        for r in rows:
            r.setdefault("Plant", "")

    # Hour slot labels for display
    for r in rows:
        r["Slot"] = hour_slot_label(r["Hour"])

    return render_template(
        "production_hourly.html",
        plant=plant,
        rows=rows,
        plant_names=list(PLANTS.keys()),
        today=selected_date.strftime("%d-%b-%Y"),
        selecteddate=selected_date.isoformat(),
        timefrom=raw_from,
        timeto=raw_to,
    )


@app.route("/api/live_total")
def api_live_total():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401

    plant = request.args.get("plant", DEFAULT_PLANT)
    if plant not in PLANTS and plant != "ALL":
        plant = DEFAULT_PLANT

    today = date.today()
    as_of = datetime.now()

    if plant == "ALL":
        overall = 0
        plant_totals = {}
        for key in PLANTS.keys():
            t = get_today_total_for_plant(key, today)
            plant_totals[key] = t
            overall += t
        return jsonify(
            {
                "plant": "ALL",
                "total": overall,
                "plant_totals": plant_totals,
                "as_of": as_of.isoformat(timespec="seconds"),
            }
        )

    total = get_today_total_for_plant(plant, today)
    return jsonify(
        {
            "plant": plant,
            "total": total,
            "as_of": as_of.isoformat(timespec="seconds"),
        }
    )


@app.route("/live")
def live_page():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    plant = request.args.get("plant", DEFAULT_PLANT)
    if plant not in PLANTS and plant != "ALL":
        plant = DEFAULT_PLANT
    today = date.today()
    return render_template(
        "live_counter.html",
        plant=plant,
        plant_names=list(PLANTS.keys()),
        today=today.strftime("%d-%b-%Y"),
    )



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=True)
