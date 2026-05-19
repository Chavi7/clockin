"""
CLOCKIN v2 — Simulated Workplace Time Clock (Multi-Teacher)
Built for a CTE classroom by Ciri.

Tech: Flask + SQLite + bcrypt. Self-hosted, firewalled-safe.
Run: python app.py
"""
import csv
import io
import json
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

import bcrypt
import qrcode
from flask import (
    Flask, g, jsonify, render_template, request, send_file, redirect,
    url_for, flash, abort, Response, session
)

# ============================================================
# CONFIG
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
# Where the SQLite DB lives. Defaults to ./data for local dev.
# Override with CLOCKIN_DATA_DIR=/data when running in Docker (the compose file
# mounts a named volume at /data so the DB persists across rebuilds).
DATA_DIR = Path(os.environ.get("CLOCKIN_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "clockin.db"
SCHEMA_PATH = BASE_DIR / "scripts" / "schema.sql"

app = Flask(__name__)
# Session secret — MUST be set to a long random string in production.
app.secret_key = os.environ.get("CLOCKIN_SECRET", "dev-secret-change-me-in-production")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)


# ============================================================
# DATABASE
# ============================================================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the database from schema.sql. Idempotent.

    Also performs lightweight migrations for older v2 databases:
      - Adds `courses` column if missing
      - Adds `username` column if missing, derives a default from email,
        and relaxes the UNIQUE NOT NULL on email
    """
    db = sqlite3.connect(DB_PATH)

    # Check what's already there before applying schema
    has_teachers = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teachers'"
    ).fetchone()

    existing_cols = []
    if has_teachers:
        existing_cols = [r[1] for r in db.execute("PRAGMA table_info(teachers)").fetchall()]

    # If teachers table exists but lacks username, we need to rebuild it
    # because adding a UNIQUE NOT NULL column with ALTER TABLE isn't possible in SQLite.
    if has_teachers and "username" not in existing_cols:
        # Migration: rebuild the table with the new schema, then copy data over,
        # deriving username from the part of email before the @.
        db.executescript("""
            CREATE TABLE teachers_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    UNIQUE NOT NULL,
                email           TEXT,
                password_hash   TEXT    NOT NULL,
                full_name       TEXT    NOT NULL,
                role            TEXT    NOT NULL DEFAULT 'teacher',
                courses         TEXT    NOT NULL DEFAULT '',
                active          INTEGER NOT NULL DEFAULT 1,
                must_reset      INTEGER NOT NULL DEFAULT 0,
                reset_token     TEXT,
                reset_expires   TEXT,
                created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
                last_login_at   TEXT
            );
        """)
        # Copy data, deriving username from email and handling possible courses-missing case
        has_courses = "courses" in existing_cols
        select_cols = "id, email, password_hash, full_name, role, active, must_reset, reset_token, reset_expires, created_at, last_login_at"
        if has_courses:
            select_cols = "id, email, password_hash, full_name, role, courses, active, must_reset, reset_token, reset_expires, created_at, last_login_at"
        rows = db.execute(f"SELECT {select_cols} FROM teachers").fetchall()

        used_usernames = set()
        for row in rows:
            if has_courses:
                (rid, email, pwhash, full_name, role, courses,
                 active, must_reset, rtoken, rexp, created, last_login) = row
            else:
                (rid, email, pwhash, full_name, role,
                 active, must_reset, rtoken, rexp, created, last_login) = row
                courses = ""

            # Derive username: take part of email before @, lowercase
            base = (email or "user").split("@")[0].lower()
            # Strip anything not [a-z0-9._-]
            base = "".join(ch for ch in base if ch.isalnum() or ch in "._-") or "user"
            username = base
            suffix = 2
            while username in used_usernames:
                username = f"{base}{suffix}"
                suffix += 1
            used_usernames.add(username)

            db.execute("""
                INSERT INTO teachers_new
                  (id, username, email, password_hash, full_name, role, courses,
                   active, must_reset, reset_token, reset_expires, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rid, username, email, pwhash, full_name, role, courses,
                  active, must_reset, rtoken, rexp, created, last_login))

        db.execute("DROP TABLE teachers")
        db.execute("ALTER TABLE teachers_new RENAME TO teachers")
        db.commit()

    # Apply schema (idempotent — won't disturb the table we just rebuilt)
    with open(SCHEMA_PATH, "r") as f:
        db.executescript(f.read())

    # Also handle a v2 DB that has username but is missing courses (shouldn't happen
    # given the above, but defensive)
    cols = [r[1] for r in db.execute("PRAGMA table_info(teachers)").fetchall()]
    if "courses" not in cols:
        db.execute("ALTER TABLE teachers ADD COLUMN courses TEXT NOT NULL DEFAULT ''")

    db.commit()
    db.close()


def is_first_run():
    """True if there are no teacher accounts yet."""
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0]
    return count == 0


# ============================================================
# AUTH HELPERS
# ============================================================
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


USERNAME_MIN = 3
USERNAME_MAX = 32

def validate_username(raw):
    """
    Returns (normalized, error_message). normalized is None if invalid.
    Rules: 3-32 chars, lowercase letters, digits, dot, underscore, hyphen.
    Stored lowercase. Reserved names blocked.
    """
    if not raw:
        return None, "Username is required."
    name = raw.strip().lower()
    if len(name) < USERNAME_MIN or len(name) > USERNAME_MAX:
        return None, f"Username must be {USERNAME_MIN}-{USERNAME_MAX} characters."
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if not all(ch in allowed for ch in name):
        return None, "Username can only contain letters, digits, dots, underscores, and hyphens."
    # Block names that would collide with route paths or be confusing
    reserved = {"admin", "root", "system", "login", "logout", "setup",
                "profile", "kiosk", "api", "static", "dashboard", "roster",
                "badges", "teachers"}
    if name in reserved:
        return None, f"'{name}' is reserved — please pick a different username."
    return name, None


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, AttributeError):
        return False


def current_user():
    """Return the current logged-in teacher row, or None."""
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return None
    db = get_db()
    return db.execute(
        "SELECT * FROM teachers WHERE id = ? AND active = 1",
        (teacher_id,),
    ).fetchone()


def login_required(view):
    """Decorator: require a logged-in teacher."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user["must_reset"]:
            # Force password change before doing anything else.
            if request.endpoint not in {"profile", "logout", "static"}:
                flash("You must change your password before continuing.", "error")
                return redirect(url_for("profile"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Decorator: require an admin role."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    """Make `current_user` available in every template."""
    return {"current_user": current_user(), "is_first_run": is_first_run()}


# ============================================================
# TIME / FORMATTING HELPERS
# ============================================================
def today_str():
    return date.today().isoformat()


def now_str():
    return datetime.now().isoformat(timespec="seconds")


def fmt_time(iso):
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%-I:%M %p")
    except (ValueError, TypeError):
        return iso


def fmt_duration(start_iso, end_iso):
    if not start_iso or not end_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        seconds = int((end - start).total_seconds())
        if seconds < 0:
            return None
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return None


# ============================================================
# PUBLIC ROUTES — KIOSK
# ============================================================
@app.route("/")
def kiosk():
    return render_template("kiosk.html")


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """
    Process a scanned QR or typed employee ID.
    Note: kiosk is public — any student from any teacher's roster can clock in here.
    """
    payload = request.get_json(silent=True) or {}
    raw = payload.get("raw", "").strip()

    employee_id = None
    if raw:
        try:
            data = json.loads(raw)
            employee_id = (data.get("employee_id") or data.get("id") or "").strip().upper()
        except (json.JSONDecodeError, AttributeError):
            employee_id = raw.strip().upper()

    if not employee_id:
        return jsonify({"ok": False, "error": "No employee ID detected in scan."}), 400

    db = get_db()
    emp = db.execute(
        "SELECT * FROM employees WHERE employee_id = ? AND active = 1",
        (employee_id,),
    ).fetchone()

    if not emp:
        return jsonify({
            "ok": False,
            "error": f"Employee ID '{employee_id}' not found. See a manager.",
        }), 404

    today = today_str()
    shift = db.execute(
        "SELECT * FROM shifts WHERE employee_id = ? AND date = ?",
        (employee_id, today),
    ).fetchone()

    now = now_str()
    display_name = f"{emp['first_name']} {emp['last_name']}"

    if shift is None:
        db.execute(
            """INSERT INTO shifts (employee_id, date, clock_in_at, period)
               VALUES (?, ?, ?, ?)""",
            (employee_id, today, now, emp["period"]),
        )
        db.commit()
        return jsonify({
            "ok": True,
            "action": "clock_in",
            "name": display_name,
            "role": emp["role"],
            "period": emp["period"],
            "time": fmt_time(now),
        })

    if shift["clock_out_at"] is None:
        db.execute(
            "UPDATE shifts SET clock_out_at = ? WHERE id = ?",
            (now, shift["id"]),
        )
        db.commit()
        duration = fmt_duration(shift["clock_in_at"], now)
        return jsonify({
            "ok": True,
            "action": "clock_out",
            "name": display_name,
            "role": emp["role"],
            "period": emp["period"],
            "time": fmt_time(now),
            "duration": duration,
        })

    return jsonify({
        "ok": False,
        "error": f"{display_name} already clocked out today at {fmt_time(shift['clock_out_at'])}.",
        "name": display_name,
    }), 409


# ============================================================
# AUTH ROUTES
# ============================================================
@app.route("/setup", methods=["GET", "POST"])
def setup():
    """
    First-run setup: create the first admin account.
    Only available when the database has zero teachers.
    """
    if not is_first_run():
        return redirect(url_for("login"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username_raw = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        courses_raw = request.form.get("courses", "").strip()

        username, username_err = validate_username(username_raw)

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if username_err:
            errors.append(username_err)
        if email and "@" not in email:
            errors.append("Email looks malformed — leave it blank if you'd rather skip it.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("setup.html",
                                   full_name=full_name, username=username_raw,
                                   email=email, courses=courses_raw)

        _, courses_stored = parse_courses_field(courses_raw)

        db = get_db()
        db.execute(
            """INSERT INTO teachers (username, email, password_hash, full_name, role, courses)
               VALUES (?, ?, ?, ?, 'admin', ?)""",
            (username, email or None, hash_password(password), full_name, courses_stored),
        )
        db.commit()
        flash("Admin account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Username + password login."""
    if is_first_run():
        return redirect(url_for("setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        teacher = db.execute(
            "SELECT * FROM teachers WHERE username = ? AND active = 1",
            (username,),
        ).fetchone()

        if not teacher or not verify_password(password, teacher["password_hash"]):
            flash("Invalid username or password.", "error")
            return render_template("login.html", username=username)

        session.clear()
        session["teacher_id"] = teacher["id"]
        session.permanent = True

        db.execute(
            "UPDATE teachers SET last_login_at = ? WHERE id = ?",
            (now_str(), teacher["id"]),
        )
        db.commit()

        next_url = request.args.get("next") or url_for("dashboard")
        return redirect(next_url)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("kiosk"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """User can change their own password and update their courses."""
    user = current_user()

    if request.method == "POST":
        action = request.form.get("action", "")

        # --- Courses update ---
        if action == "update_courses":
            courses_raw = request.form.get("courses", "").strip()
            _, courses_stored = parse_courses_field(courses_raw)
            db = get_db()
            db.execute(
                "UPDATE teachers SET courses = ? WHERE id = ?",
                (courses_stored, user["id"]),
            )
            db.commit()
            if courses_stored:
                flash(f"Courses updated: {courses_stored}", "success")
            else:
                flash("Courses cleared.", "success")
            return redirect(url_for("profile"))

        # --- Email update ---
        if action == "update_email":
            email = request.form.get("email", "").strip().lower()
            if email and "@" not in email:
                flash("Email looks malformed — leave it blank if you'd rather skip it.", "error")
                return redirect(url_for("profile"))
            db = get_db()
            db.execute(
                "UPDATE teachers SET email = ? WHERE id = ?",
                (email or None, user["id"]),
            )
            db.commit()
            if email:
                flash(f"Email updated to {email}.", "success")
            else:
                flash("Email cleared.", "success")
            return redirect(url_for("profile"))

        # --- Password change ---
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")

        # If must_reset is set, skip current password check (admin issued reset)
        if not user["must_reset"]:
            if not verify_password(current_pw, user["password_hash"]):
                flash("Current password is incorrect.", "error")
                return render_template("profile.html")

        if len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
            return render_template("profile.html")
        if new_pw != confirm:
            flash("New passwords do not match.", "error")
            return render_template("profile.html")

        db = get_db()
        db.execute(
            "UPDATE teachers SET password_hash = ?, must_reset = 0 WHERE id = ?",
            (hash_password(new_pw), user["id"]),
        )
        db.commit()
        flash("Password updated successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("profile.html")


# ============================================================
# DASHBOARD
# ============================================================
@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    db = get_db()
    today = today_str()
    period_filter = request.args.get("period", "").strip()

    # Admins see all teachers' students; teachers see only their own
    view_all = user["role"] == "admin" and request.args.get("scope") == "all"

    sql = """
        SELECT e.employee_id, e.first_name, e.last_name, e.role, e.period,
               e.owner_teacher_id,
               t.full_name AS owner_name,
               s.clock_in_at, s.clock_out_at, s.id AS shift_id
        FROM employees e
        LEFT JOIN teachers t ON t.id = e.owner_teacher_id
        LEFT JOIN shifts s
          ON s.employee_id = e.employee_id AND s.date = ?
        WHERE e.active = 1
    """
    params = [today]

    if not view_all:
        sql += " AND e.owner_teacher_id = ?"
        params.append(user["id"])

    if period_filter:
        sql += " AND e.period = ?"
        params.append(period_filter)

    sql += " ORDER BY e.period, e.last_name, e.first_name"
    rows = db.execute(sql, params).fetchall()

    employees = []
    stats = {"total": 0, "clocked_in": 0, "clocked_out": 0, "absent": 0}
    for r in rows:
        stats["total"] += 1
        in_t = r["clock_in_at"]
        out_t = r["clock_out_at"]
        if in_t and not out_t:
            status = "in"
            stats["clocked_in"] += 1
        elif in_t and out_t:
            status = "out"
            stats["clocked_out"] += 1
        else:
            status = "absent"
            stats["absent"] += 1
        employees.append({
            "employee_id": r["employee_id"],
            "name": f"{r['first_name']} {r['last_name']}",
            "role": r["role"] or "—",
            "period": r["period"] or "—",
            "owner_name": r["owner_name"] or "—",
            "clock_in": fmt_time(in_t),
            "clock_out": fmt_time(out_t),
            "duration": fmt_duration(in_t, out_t) or "—",
            "status": status,
        })

    # Period list scoped to what this user can see
    period_sql = "SELECT DISTINCT period FROM employees WHERE active = 1 AND period IS NOT NULL"
    period_params = []
    if not view_all:
        period_sql += " AND owner_teacher_id = ?"
        period_params.append(user["id"])
    period_sql += " ORDER BY period"
    periods = [row["period"] for row in db.execute(period_sql, period_params).fetchall()]

    return render_template(
        "dashboard.html",
        employees=employees,
        stats=stats,
        periods=periods,
        active_period=period_filter,
        view_all=view_all,
        today=today,
    )


@app.route("/dashboard/export.csv")
@login_required
def dashboard_export():
    user = current_user()
    db = get_db()
    today = today_str()
    view_all = user["role"] == "admin" and request.args.get("scope") == "all"

    sql = """
        SELECT e.period, e.employee_id, e.last_name, e.first_name, e.role,
               t.full_name AS owner_name,
               s.clock_in_at, s.clock_out_at
        FROM employees e
        LEFT JOIN teachers t ON t.id = e.owner_teacher_id
        LEFT JOIN shifts s
          ON s.employee_id = e.employee_id AND s.date = ?
        WHERE e.active = 1
    """
    params = [today]
    if not view_all:
        sql += " AND e.owner_teacher_id = ?"
        params.append(user["id"])
    sql += " ORDER BY e.period, e.last_name, e.first_name"

    rows = db.execute(sql, params).fetchall()

    out = io.StringIO()
    writer = csv.writer(out)
    header = ["Period", "Employee ID", "Last Name", "First Name", "Role",
              "Clock In", "Clock Out", "Duration", "Status"]
    if view_all:
        header.insert(5, "Owner Teacher")
    writer.writerow(header)

    for r in rows:
        in_t, out_t = r["clock_in_at"], r["clock_out_at"]
        if in_t and not out_t:
            status = "CLOCKED IN"
        elif in_t and out_t:
            status = "CLOCKED OUT"
        else:
            status = "ABSENT"
        row = [
            r["period"] or "",
            r["employee_id"],
            r["last_name"],
            r["first_name"],
            r["role"] or "",
            fmt_time(in_t) if in_t else "",
            fmt_time(out_t) if out_t else "",
            fmt_duration(in_t, out_t) or "",
            status,
        ]
        if view_all:
            row.insert(5, r["owner_name"] or "")
        writer.writerow(row)

    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=attendance_{today}.csv"},
    )


# ============================================================
# ROSTER MANAGEMENT
# ============================================================
@app.route("/roster")
@login_required
def roster():
    user = current_user()
    db = get_db()
    view_all = user["role"] == "admin" and request.args.get("scope") == "all"

    sql = """
        SELECT e.*, t.full_name AS owner_name
        FROM employees e
        LEFT JOIN teachers t ON t.id = e.owner_teacher_id
    """
    params = []
    if not view_all:
        sql += " WHERE e.owner_teacher_id = ?"
        params.append(user["id"])
    sql += " ORDER BY e.active DESC, e.period, e.last_name, e.first_name"

    employees = db.execute(sql, params).fetchall()
    return render_template("roster.html", employees=employees, view_all=view_all)


# ------------------------------------------------------------
# Period normalization. The system only stores 'A.M.' or 'P.M.'
# Anything else gets stored as the empty string (unassigned).
#
# Recognized inputs (case-insensitive):
#   'am', 'a.m.', 'a.m', 'morning', '1' -> 'A.M.'
#   'pm', 'p.m.', 'p.m', 'afternoon', '2' -> 'P.M.'
# ------------------------------------------------------------
PERIOD_VALUES = ("A.M.", "P.M.")

_PERIOD_LOOKUP = {
    "am": "A.M.",
    "a.m.": "A.M.",
    "a.m": "A.M.",
    "a m": "A.M.",
    "morning": "A.M.",
    "1": "A.M.",
    "1st": "A.M.",
    "first": "A.M.",
    "pm": "P.M.",
    "p.m.": "P.M.",
    "p.m": "P.M.",
    "p m": "P.M.",
    "afternoon": "P.M.",
    "2": "P.M.",
    "2nd": "P.M.",
    "second": "P.M.",
}


def normalize_period(raw):
    """
    Given any reasonable variant a teacher or CSV might type, return
    'A.M.', 'P.M.', or '' if we can't make sense of it.
    """
    if not raw:
        return ""
    key = " ".join(str(raw).strip().lower().split())
    return _PERIOD_LOOKUP.get(key, "")



#   1. Auto-generating Employee ID prefixes (existing).
#   2. Recognizing what a teacher typed in their free-text courses field,
#      so we can quietly match "Cyber 1" to "Cybersecurity 1".
#
# CANONICAL_COURSES maps a normalized lookup key (lowercase, single spaces)
# to a canonical display name. The display name is what we show in the UI
# and use for matching. The lookup keys are the variants we recognize.
# ------------------------------------------------------------
CANONICAL_COURSES = {
    "IT Fundamentals":          ["it fundamentals", "it fund", "itf", "fundamentals",
                                 "it fund.", "it fundementals"],  # common typo
    "Cybersecurity 1":          ["cybersecurity 1", "cybersecurity i", "cyber 1", "cyber i",
                                 "cyb1", "cybersecurity", "cyber sec 1", "cybersec 1"],
    "Cybersecurity 2":          ["cybersecurity 2", "cybersecurity ii", "cyber 2", "cyber ii",
                                 "cyb2", "cyber sec 2", "cybersec 2"],
    "Computer Engineering 1":   ["computer engineering 1", "computer engineering i",
                                 "comp eng 1", "compe 1", "ce1", "computer engineering",
                                 "comp engineering 1", "ce 1"],
    "Computer Engineering 2":   ["computer engineering 2", "computer engineering ii",
                                 "comp eng 2", "compe 2", "ce2", "comp engineering 2",
                                 "ce 2"],
}

# Reverse lookup: normalized key -> canonical display name
_LOOKUP = {}
for canonical, variants in CANONICAL_COURSES.items():
    for v in variants:
        _LOOKUP[v] = canonical
    _LOOKUP[canonical.lower()] = canonical


def normalize_course_name(raw):
    """
    Given any string a teacher (or CSV) might type, return either:
      - the canonical display name if we recognize it, or
      - the raw text (stripped) if we don't.

    Never raises. Empty input returns empty string.
    """
    if not raw:
        return ""
    key = " ".join(raw.strip().lower().split())
    return _LOOKUP.get(key, raw.strip())


def parse_courses_field(raw):
    """
    Parse the free-text 'courses' field (e.g. 'IT Fund, Cyber 1; Web Design').
    Splits on commas and semicolons. Returns:
      - normalized: list of canonical display names (deduplicated, order preserved)
      - raw_stored: same list joined by ', ' for storage
    """
    if not raw:
        return [], ""
    # Split on , or ;
    import re
    parts = re.split(r"[,;]", raw)
    seen = set()
    normalized = []
    for p in parts:
        name = normalize_course_name(p)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            normalized.append(name)
    return normalized, ", ".join(normalized)


# ------------------------------------------------------------
# Course-to-prefix mapping for auto-generated Employee IDs.
# (Used by roster upload; lives below for historical placement.)
# ------------------------------------------------------------
COURSE_PREFIXES = {
    # IT Fundamentals
    "it fundamentals":           "ITF",
    "it fund":                   "ITF",
    "itf":                       "ITF",
    "fundamentals":              "ITF",
    # Cybersecurity 1
    "cybersecurity 1":           "CYB1",
    "cybersecurity i":           "CYB1",
    "cyber 1":                   "CYB1",
    "cyber i":                   "CYB1",
    "cyb1":                      "CYB1",
    "cybersecurity":             "CYB1",  # ambiguous default to 1
    # Cybersecurity 2
    "cybersecurity 2":           "CYB2",
    "cybersecurity ii":          "CYB2",
    "cyber 2":                   "CYB2",
    "cyber ii":                  "CYB2",
    "cyb2":                      "CYB2",
    # Computer Engineering 1
    "computer engineering 1":    "CE1",
    "computer engineering i":    "CE1",
    "comp eng 1":                "CE1",
    "compe 1":                   "CE1",
    "ce1":                       "CE1",
    "computer engineering":      "CE1",  # ambiguous default to 1
    # Computer Engineering 2
    "computer engineering 2":    "CE2",
    "computer engineering ii":   "CE2",
    "comp eng 2":                "CE2",
    "compe 2":                   "CE2",
    "ce2":                       "CE2",
}
FALLBACK_PREFIX = "STU"


def prefix_for_course(course_raw):
    """Return (prefix, used_fallback). used_fallback is True if we couldn't match."""
    if not course_raw:
        return FALLBACK_PREFIX, True
    key = " ".join(course_raw.strip().lower().split())  # collapse whitespace
    if key in COURSE_PREFIXES:
        return COURSE_PREFIXES[key], False
    return FALLBACK_PREFIX, True


def next_employee_id(db, prefix):
    """
    Return the next available employee_id for a given prefix.
    Looks at existing IDs starting with PREFIX- and picks max(N)+1, zero-padded to 3.
    """
    rows = db.execute(
        "SELECT employee_id FROM employees WHERE employee_id LIKE ? ORDER BY employee_id",
        (f"{prefix}-%",),
    ).fetchall()
    max_num = 0
    for r in rows:
        suffix = r["employee_id"][len(prefix) + 1:]
        if suffix.isdigit():
            n = int(suffix)
            if n > max_num:
                max_num = n
    return f"{prefix}-{max_num + 1:03d}"


@app.route("/roster/upload", methods=["POST"])
@login_required
def upload_roster():
    user = current_user()
    file = request.files.get("roster")
    if not file:
        flash("No file uploaded.", "error")
        return redirect(url_for("roster"))

    try:
        text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("File must be UTF-8 encoded CSV.", "error")
        return redirect(url_for("roster"))

    reader = csv.DictReader(io.StringIO(text))
    required = {"employee_id", "first_name", "last_name", "school"}
    if not required.issubset({h.strip().lower() for h in reader.fieldnames or []}):
        flash(
            f"CSV missing required columns. Need at least: {', '.join(sorted(required))}.",
            "error",
        )
        return redirect(url_for("roster"))

    db = get_db()
    added, updated, conflicts = 0, 0, []
    generated_ids = []           # rows where we filled in the ID
    fallback_rows = []           # rows that used STU- fallback
    bad_period_rows = []         # rows whose period couldn't be normalized

    # Track IDs we've already assigned in this upload so we don't double-pick.
    # next_employee_id() reads existing DB rows, but it doesn't see uncommitted
    # picks from earlier rows in *this same loop*.
    assigned_in_batch = {}  # prefix -> last-used number

    for line_num, row in enumerate(reader, start=2):  # line 2 = first data row after header
        norm = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items()}

        # Skip totally blank rows
        if not any(norm.get(k) for k in ("employee_id", "first_name", "last_name")):
            continue

        # Normalize period: only 'A.M.' or 'P.M.' are accepted; everything else is blank.
        raw_period = norm.get("period", "")
        period_value = normalize_period(raw_period)
        if raw_period and not period_value:
            bad_period_rows.append(f"{norm.get('first_name','')} {norm.get('last_name','')} (line {line_num}, value: {raw_period!r})")
        norm["period"] = period_value

        # Name is required
        if not norm.get("first_name") or not norm.get("last_name"):
            flash(f"Row {line_num}: missing first or last name — skipped.", "warning")
            continue

        eid = norm.get("employee_id", "").upper().strip()

        # Auto-generate ID if blank
        if not eid:
            prefix, used_fallback = prefix_for_course(norm.get("course"))
            if used_fallback:
                fallback_rows.append(f"{norm['first_name']} {norm['last_name']}")

            # Find the next number, considering both DB and this-upload assignments
            if prefix in assigned_in_batch:
                next_num = assigned_in_batch[prefix] + 1
                # But still check DB in case someone else added rows; pick max
                db_next = next_employee_id(db, prefix)
                db_num = int(db_next.split("-")[1])
                next_num = max(next_num, db_num)
            else:
                db_next = next_employee_id(db, prefix)
                next_num = int(db_next.split("-")[1])

            assigned_in_batch[prefix] = next_num
            eid = f"{prefix}-{next_num:03d}"
            generated_ids.append(eid)

        existing = db.execute(
            """SELECT e.id, e.owner_teacher_id, t.full_name AS owner_name
               FROM employees e
               LEFT JOIN teachers t ON t.id = e.owner_teacher_id
               WHERE e.employee_id = ?""",
            (eid,),
        ).fetchone()

        if existing:
            if existing["owner_teacher_id"] == user["id"]:
                # Owned by current user — safe to update
                db.execute("""
                    UPDATE employees
                    SET school = ?, first_name = ?, last_name = ?, student_id = ?,
                        role = ?, course = ?, period = ?, active = 1
                    WHERE employee_id = ?
                """, (
                    norm.get("school", ""),
                    norm["first_name"],
                    norm["last_name"],
                    norm.get("student_id", ""),
                    norm.get("role", ""),
                    norm.get("course", ""),
                    norm.get("period", ""),
                    eid,
                ))
                updated += 1
            else:
                # Owned by someone else — skip and record conflict
                conflicts.append({
                    "employee_id": eid,
                    "name": f"{norm['first_name']} {norm['last_name']}",
                    "owner": existing["owner_name"] or "another teacher",
                })
        else:
            db.execute("""
                INSERT INTO employees
                  (employee_id, school, first_name, last_name, student_id,
                   role, course, period, owner_teacher_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                eid,
                norm.get("school", ""),
                norm["first_name"],
                norm["last_name"],
                norm.get("student_id", ""),
                norm.get("role", ""),
                norm.get("course", ""),
                norm.get("period", ""),
                user["id"],
            ))
            added += 1

    db.commit()

    msg = f"Roster updated. Added {added}, updated {updated}."
    if generated_ids:
        msg += f" Generated {len(generated_ids)} new Employee IDs."
    flash(msg, "success" if not conflicts else "warning")

    if generated_ids and len(generated_ids) <= 10:
        flash("Generated IDs: " + ", ".join(generated_ids), "info")
    elif generated_ids:
        flash(f"Generated IDs: {', '.join(generated_ids[:8])}, …and {len(generated_ids) - 8} more.", "info")

    if fallback_rows:
        flash(
            f"⚠ {len(fallback_rows)} student(s) had no recognizable course — "
            f"used the generic STU- prefix. Edit the CSV to add a course and re-upload "
            f"if you want a course-specific ID.",
            "warning",
        )

    if bad_period_rows:
        flash(
            f"⚠ {len(bad_period_rows)} row(s) had an unrecognized period value — "
            f"left blank. Use 'A.M.' or 'P.M.' in the period column.",
            "warning",
        )
        for row_desc in bad_period_rows[:5]:
            flash(f"⚠ {row_desc}", "warning")
        if len(bad_period_rows) > 5:
            flash(f"…and {len(bad_period_rows) - 5} more.", "warning")

    if conflicts:
        flash(f"Skipped {len(conflicts)} student(s) already owned by another teacher.", "warning")
        for c in conflicts[:10]:
            flash(f"⚠ {c['employee_id']} {c['name']} is owned by {c['owner']}.", "warning")
        if len(conflicts) > 10:
            flash(f"…and {len(conflicts) - 10} more conflicts.", "warning")
        if user["role"] == "admin":
            flash("Tip: as admin, you can reassign ownership from the Roster page.", "info")

    return redirect(url_for("roster"))


@app.route("/roster/template/blank.csv")
@login_required
def roster_template_blank():
    """Empty CSV with just the headers."""
    headers = ["employee_id", "first_name", "last_name", "school",
               "student_id", "role", "course", "period"]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    # One empty row to make it obvious where data goes
    writer.writerow([""] * len(headers))
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster_template_blank.csv"},
    )


@app.route("/roster/template/example.csv")
@login_required
def roster_template_example():
    """CSV with example rows showing valid data and column conventions."""
    headers = ["employee_id", "first_name", "last_name", "school",
               "student_id", "role", "course", "period"]
    rows = [
        # Leave employee_id BLANK to auto-generate — these will become ITF-001, ITF-002, etc.
        # The 'period' column is either 'A.M.' or 'P.M.' (these are the only accepted values).
        ["", "Alex", "Rivera", "Lincoln High", "128431",
         "Help Desk Manager", "IT Fundamentals", "A.M."],
        ["", "Sam", "Chen", "Lincoln High", "128765",
         "Inventory Manager", "IT Fundamentals", "A.M."],
        ["", "Jordan", "Patel", "Lincoln High", "128902",
         "6S Manager", "IT Fundamentals", "A.M."],

        # Cybersecurity 1 — will become CYB1-001, CYB1-002
        ["", "Bailey", "Walsh", "Lincoln High", "131005",
         "Help Desk Manager", "Cybersecurity 1", "P.M."],
        ["", "Hayden", "Mercer", "Lincoln High", "131120",
         "SOC Analyst", "Cybersecurity 1", "P.M."],

        # Computer Engineering 1 — will become CE1-001
        ["", "Drew", "Larson", "Lincoln High", "132011",
         "Field Tech Lead", "Computer Engineering 1", "A.M."],

        # Override the auto-generated ID with a custom one
        ["CUSTOM-007", "Indy", "Calloway", "Lincoln High", "133007",
         "Senior Technician", "Computer Engineering 2", "P.M."],
    ]

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(r)
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster_template_example.csv"},
    )


@app.route("/roster/<int:eid>/toggle", methods=["POST"])
@login_required
def toggle_employee(eid):
    user = current_user()
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (eid,)).fetchone()
    if not emp:
        abort(404)
    if user["role"] != "admin" and emp["owner_teacher_id"] != user["id"]:
        abort(403)
    db.execute("UPDATE employees SET active = 1 - active WHERE id = ?", (eid,))
    db.commit()
    return redirect(url_for("roster"))


@app.route("/roster/<int:eid>/reassign", methods=["POST"])
@admin_required
def reassign_employee(eid):
    """Admin-only: move an employee to a different teacher's roster."""
    new_owner_id = request.form.get("owner_id", type=int)
    db = get_db()
    if not db.execute("SELECT 1 FROM teachers WHERE id = ?", (new_owner_id,)).fetchone():
        flash("Invalid teacher.", "error")
        return redirect(url_for("roster", scope="all"))
    db.execute(
        "UPDATE employees SET owner_teacher_id = ? WHERE id = ?",
        (new_owner_id, eid),
    )
    db.commit()
    flash("Employee reassigned.", "success")
    return redirect(url_for("roster", scope="all"))


@app.route("/roster/<int:eid>/edit", methods=["GET", "POST"])
@login_required
def edit_employee(eid):
    """
    Edit an employee's role, period, and course.
    Role changes are logged to role_history; period and course are not.
    """
    user = current_user()
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (eid,)).fetchone()
    if not emp:
        abort(404)
    # Authorization: owner or admin
    if user["role"] != "admin" and emp["owner_teacher_id"] != user["id"]:
        abort(403)

    if request.method == "POST":
        new_role   = request.form.get("role", "").strip()
        new_period = normalize_period(request.form.get("period", ""))
        new_course_raw = request.form.get("course", "").strip()
        # Normalize course through the same canonical map used elsewhere
        new_course = normalize_course_name(new_course_raw) if new_course_raw else ""
        note = request.form.get("note", "").strip() or None

        old_role = emp["role"] or ""

        # Apply the update
        db.execute(
            "UPDATE employees SET role = ?, period = ?, course = ? WHERE id = ?",
            (new_role, new_period, new_course, eid),
        )

        # Log role changes ONLY (per design decision)
        if new_role != old_role:
            db.execute(
                """INSERT INTO role_history
                   (employee_id, old_role, new_role, changed_by, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (emp["employee_id"], old_role, new_role, user["id"], note),
            )

        db.commit()

        if new_role != old_role:
            flash(
                f"{emp['first_name']} {emp['last_name']}: role changed from "
                f"\"{old_role or '(none)'}\" to \"{new_role or '(none)'}\".",
                "success",
            )
        else:
            flash(f"{emp['first_name']} {emp['last_name']} updated.", "success")

        return redirect(url_for("roster"))

    return render_template("employee_edit.html", emp=emp)


@app.route("/roster/<int:eid>/history")
@login_required
def employee_history(eid):
    """View role-change history for a single employee."""
    user = current_user()
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (eid,)).fetchone()
    if not emp:
        abort(404)
    if user["role"] != "admin" and emp["owner_teacher_id"] != user["id"]:
        abort(403)

    history = db.execute("""
        SELECT h.*, t.full_name AS changed_by_name, t.username AS changed_by_username
        FROM role_history h
        LEFT JOIN teachers t ON t.id = h.changed_by
        WHERE h.employee_id = ?
        ORDER BY h.changed_at DESC
    """, (emp["employee_id"],)).fetchall()

    return render_template("employee_history.html", emp=emp, history=history)


@app.route("/roster/<int:eid>/delete", methods=["GET", "POST"])
@login_required
def delete_employee(eid):
    """
    Permanently delete an employee, their shifts, and their role history.
    Two-step: GET shows confirmation page with counts; POST performs delete.
    Owner of the employee OR any admin can do this.
    """
    user = current_user()
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (eid,)).fetchone()
    if not emp:
        abort(404)
    # Owner or admin only
    if user["role"] != "admin" and emp["owner_teacher_id"] != user["id"]:
        abort(403)

    # Count what'll be wiped
    shift_count = db.execute(
        "SELECT COUNT(*) FROM shifts WHERE employee_id = ?",
        (emp["employee_id"],),
    ).fetchone()[0]
    history_count = db.execute(
        "SELECT COUNT(*) FROM role_history WHERE employee_id = ?",
        (emp["employee_id"],),
    ).fetchone()[0]

    if request.method == "POST":
        # Safety check: the form must include a confirmation token equal to the
        # employee_id. This prevents accidental deletes from a stale form / CSRF.
        typed_confirmation = request.form.get("confirm_id", "").strip().upper()
        if typed_confirmation != emp["employee_id"]:
            flash(
                f"Confirmation didn't match. Type '{emp['employee_id']}' exactly to confirm.",
                "error",
            )
            return render_template(
                "employee_delete.html",
                emp=emp, shift_count=shift_count, history_count=history_count,
            )

        # Order matters: delete dependent rows first to keep FK constraints happy
        db.execute("DELETE FROM role_history WHERE employee_id = ?", (emp["employee_id"],))
        db.execute("DELETE FROM shifts WHERE employee_id = ?", (emp["employee_id"],))
        db.execute("DELETE FROM employees WHERE id = ?", (eid,))
        db.commit()

        flash(
            f"Permanently deleted {emp['first_name']} {emp['last_name']} "
            f"({emp['employee_id']}), {shift_count} shift(s), and {history_count} role-history entr"
            f"{'ies' if history_count != 1 else 'y'}.",
            "success",
        )
        return redirect(url_for("roster"))

    return render_template(
        "employee_delete.html",
        emp=emp, shift_count=shift_count, history_count=history_count,
    )


# ============================================================
# BADGES
# ============================================================
@app.route("/badges")
@login_required
def badges_form():
    user = current_user()
    db = get_db()
    sql = "SELECT DISTINCT period FROM employees WHERE active = 1 AND period IS NOT NULL"
    params = []
    if user["role"] != "admin" or request.args.get("scope") != "all":
        sql += " AND owner_teacher_id = ?"
        params.append(user["id"])
    sql += " ORDER BY period"
    periods = [row["period"] for row in db.execute(sql, params).fetchall()]
    return render_template("badges.html", periods=periods)


@app.route("/badges.pdf")
@login_required
def badges_pdf():
    user = current_user()
    period = request.args.get("period", "").strip()
    scope = request.args.get("scope", "")

    db = get_db()
    sql = "SELECT * FROM employees WHERE active = 1"
    params = []

    if user["role"] != "admin" or scope != "all":
        sql += " AND owner_teacher_id = ?"
        params.append(user["id"])
    if period:
        sql += " AND period = ?"
        params.append(period)
    sql += " ORDER BY last_name, first_name"

    employees = db.execute(sql, params).fetchall()

    if not employees:
        flash("No active employees match that filter.", "error")
        return redirect(url_for("badges_form"))

    pdf_bytes = generate_badges_pdf(employees)
    filename = f"badges{'_' + period if period else ''}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


def generate_badges_pdf(employees):
    """8 badges per US-letter page, 2x4 grid."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    page_w, page_h = letter

    badge_w = 3.5 * inch
    badge_h = 2.25 * inch
    cols, rows = 2, 4
    margin_x = (page_w - cols * badge_w) / 2
    margin_y = (page_h - rows * badge_h) / 2

    for i, emp in enumerate(employees):
        page_idx = i // (cols * rows)
        pos = i % (cols * rows)
        col = pos % cols
        row = pos // cols

        if pos == 0 and page_idx > 0:
            c.showPage()

        x = margin_x + col * badge_w
        y = page_h - margin_y - (row + 1) * badge_h

        draw_badge(c, x, y, badge_w, badge_h, emp, ImageReader)

    c.save()
    return buf.getvalue()


def draw_badge(c, x, y, w, h, emp, ImageReader):
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor

    navy = HexColor("#0F2540")
    amber = HexColor("#F59E0B")
    cream = HexColor("#FAF7F2")
    grey = HexColor("#6B7280")

    c.setFillColor(cream)
    c.rect(x, y, w, h, fill=1, stroke=0)

    header_h = 0.42 * inch
    c.setFillColor(navy)
    c.rect(x, y + h - header_h, w, header_h, fill=1, stroke=0)

    c.setFillColor(amber)
    c.rect(x, y + h - header_h - 0.04 * inch, w, 0.04 * inch, fill=1, stroke=0)

    c.setFillColor(cream)
    c.setFont("Helvetica-Bold", 11)
    school = (emp["school"] or "OFFICE").upper()
    c.drawString(x + 0.15 * inch, y + h - header_h + 0.22 * inch, school)
    c.setFont("Helvetica", 7)
    c.drawString(x + 0.15 * inch, y + h - header_h + 0.10 * inch, "SIMULATED WORKPLACE · EMPLOYEE ID")

    c.setStrokeColor(navy)
    c.setLineWidth(1.2)
    c.rect(x, y, w, h, fill=0, stroke=1)

    qr_data = json.dumps({
        "school": emp["school"] or "",
        "name": f"{emp['first_name']} {emp['last_name']}",
        "employee_id": emp["employee_id"],
        "student_id": emp["student_id"] or "",
    }, separators=(",", ":"))
    qr_img = make_qr_image(qr_data)
    qr_size = 1.35 * inch
    qr_x = x + w - qr_size - 0.12 * inch
    qr_y = y + (h - header_h - qr_size) / 2 + 0.02 * inch
    c.drawImage(qr_img, qr_x, qr_y, qr_size, qr_size, mask="auto")

    tx = x + 0.15 * inch
    ty = y + h - header_h - 0.32 * inch

    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(tx, ty, emp["first_name"] or "")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(tx, ty - 0.18 * inch, emp["last_name"] or "")

    c.setFillColor(grey)
    c.setFont("Helvetica", 7)
    c.drawString(tx, ty - 0.40 * inch, "ROLE")
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 8.5)
    role = (emp["role"] or "EMPLOYEE")[:24]
    c.drawString(tx, ty - 0.52 * inch, role.upper())

    c.setFillColor(grey)
    c.setFont("Helvetica", 7)
    c.drawString(tx, ty - 0.70 * inch, "ID")
    c.setFillColor(navy)
    c.setFont("Courier-Bold", 11)
    c.drawString(tx, ty - 0.85 * inch, emp["employee_id"])

    c.setFillColor(grey)
    c.setFont("Helvetica-Oblique", 6)
    c.drawString(x + 0.15 * inch, y + 0.10 * inch,
                 "Property of the office. If found, return to your supervisor.")


def make_qr_image(data):
    from reportlab.lib.utils import ImageReader
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0F2540", back_color="#FAF7F2")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


# ============================================================
# TEACHER MANAGEMENT (admin only)
# ============================================================
@app.route("/teachers")
@admin_required
def teachers_list():
    db = get_db()
    teachers = db.execute("""
        SELECT t.*,
               (SELECT COUNT(*) FROM employees WHERE owner_teacher_id = t.id AND active = 1)
                 AS employee_count
        FROM teachers t
        ORDER BY t.active DESC, t.role DESC, t.full_name
    """).fetchall()
    return render_template("teachers.html", teachers=teachers)


@app.route("/teachers/new", methods=["GET", "POST"])
@admin_required
def teacher_new():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username_raw = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "teacher")
        courses_raw = request.form.get("courses", "").strip()

        username, username_err = validate_username(username_raw)

        errors = []
        if not full_name:
            errors.append("Full name is required.")
        if username_err:
            errors.append(username_err)
        if email and "@" not in email:
            errors.append("Email looks malformed — leave it blank if you'd rather skip it.")
        if len(password) < 8:
            errors.append("Initial password must be at least 8 characters.")
        if role not in ("teacher", "admin"):
            role = "teacher"

        db = get_db()
        if username and db.execute("SELECT 1 FROM teachers WHERE username = ?", (username,)).fetchone():
            errors.append(f"A teacher with username '{username}' already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("teacher_form.html",
                                   full_name=full_name, username=username_raw,
                                   email=email, role=role, courses=courses_raw)

        _, courses_stored = parse_courses_field(courses_raw)

        db.execute(
            """INSERT INTO teachers (username, email, password_hash, full_name, role, courses, must_reset)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (username, email or None, hash_password(password), full_name, role, courses_stored),
        )
        db.commit()
        flash(
            f"Account created for {full_name}. Username: {username}. "
            f"They'll be required to change the initial password on first login.",
            "success",
        )
        return redirect(url_for("teachers_list"))

    return render_template("teacher_form.html")


@app.route("/teachers/<int:tid>/toggle", methods=["POST"])
@admin_required
def teacher_toggle(tid):
    user = current_user()
    if tid == user["id"]:
        flash("You can't deactivate yourself.", "error")
        return redirect(url_for("teachers_list"))
    db = get_db()
    db.execute("UPDATE teachers SET active = 1 - active WHERE id = ?", (tid,))
    db.commit()
    return redirect(url_for("teachers_list"))


@app.route("/teachers/<int:tid>/role", methods=["POST"])
@admin_required
def teacher_role(tid):
    """Promote/demote a teacher between 'teacher' and 'admin'."""
    user = current_user()
    if tid == user["id"]:
        flash("You can't change your own role. Have another admin do it.", "error")
        return redirect(url_for("teachers_list"))

    new_role = request.form.get("role", "")
    if new_role not in ("teacher", "admin"):
        abort(400)

    db = get_db()
    # Guard: never let the system end up with zero admins
    if new_role == "teacher":
        admin_count = db.execute(
            "SELECT COUNT(*) FROM teachers WHERE role = 'admin' AND active = 1"
        ).fetchone()[0]
        if admin_count <= 1:
            target = db.execute("SELECT role FROM teachers WHERE id = ?", (tid,)).fetchone()
            if target and target["role"] == "admin":
                flash("Cannot demote: this is the last active admin.", "error")
                return redirect(url_for("teachers_list"))

    db.execute("UPDATE teachers SET role = ? WHERE id = ?", (new_role, tid))
    db.commit()
    flash(f"Role updated to {new_role}.", "success")
    return redirect(url_for("teachers_list"))


@app.route("/teachers/<int:tid>/reset_password", methods=["POST"])
@admin_required
def teacher_reset_password(tid):
    """Generate a temporary password for a teacher; force them to change it on next login."""
    db = get_db()
    teacher = db.execute("SELECT * FROM teachers WHERE id = ?", (tid,)).fetchone()
    if not teacher:
        abort(404)
    temp = secrets.token_urlsafe(9)
    db.execute(
        "UPDATE teachers SET password_hash = ?, must_reset = 1 WHERE id = ?",
        (hash_password(temp), tid),
    )
    db.commit()
    flash(
        f"Temporary password for {teacher['full_name']}: {temp} — give it to them in person. "
        f"They'll be required to change it on next login.",
        "warning",
    )
    return redirect(url_for("teachers_list"))


# ============================================================
# ERROR HANDLERS
# ============================================================
@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403,
                           message="You don't have permission to view this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404,
                           message="That page doesn't exist."), 404


# ============================================================
# BOOTSTRAP
# ============================================================
def ensure_initialized():
    """Create / migrate the schema. Safe to call repeatedly."""
    init_db()


# Initialize at import time so both `python app.py` AND
# gunicorn workers (which import `app:app`) get a ready DB.
ensure_initialized()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
