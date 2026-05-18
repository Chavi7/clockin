# CLOCKIN v2 — The Office Time Clock (Multi-Teacher)

A simulated-workplace QR-badge time clock for CTE classrooms.
Built by **Ciri** for the AI-powered classroom operations ecosystem.

**What's new in v2:** Real teacher accounts. Each teacher signs in with their own
email and password, sees only their own roster, and can print only their own badges.
Admins can see everything, manage accounts, and resolve roster conflicts.

---

## What's in the box

```
clockin/
├── app.py                    # Flask app with auth, roles, multi-teacher
├── requirements.txt
├── sample_roster.csv
├── scripts/
│   └── schema.sql            # teachers + employees + shifts tables
├── templates/
│   ├── base.html
│   ├── _nav.html             # shared nav bar partial
│   ├── kiosk.html            # public time clock
│   ├── setup.html            # first-run admin account creation
│   ├── login.html
│   ├── profile.html          # change own password
│   ├── dashboard.html
│   ├── roster.html
│   ├── badges.html
│   ├── teachers.html         # admin: list of teacher accounts
│   ├── teacher_form.html     # admin: add new teacher
│   └── error.html
├── static/
│   ├── css/styles.css
│   └── js/
│       ├── jsQR.js
│       └── kiosk.js
└── data/                     # SQLite database lives here (auto-created)
```

---

## How the multi-teacher model works

**Login identifier: username, not email.** Each teacher picks a username (3-32 characters; letters, digits, dots, underscores, hyphens). Usernames are case-insensitive — `Chavis` and `chavis` are the same account. Email is optional and used only for future password reset features.



**Roles:**
- **Admin** — full access. Sees every teacher's roster and today's activity. Creates teacher accounts. Promotes/demotes. Resets passwords. Reassigns student ownership.
- **Teacher** — sees only their own roster. Uploads their own CSV. Prints their own badges.

**Student ownership:**
- Every student belongs to exactly one teacher (the one who uploaded them).
- If a second teacher uploads a CSV containing a student already owned by someone else, that student is **skipped** and a warning is shown. No silent overwrites.
- Admins can reassign ownership manually from the Roster page in "ALL TEACHERS" view.

**The kiosk is shared:**
- One public time clock URL for the whole school. Any student from any teacher's roster can clock in from it. The dashboard for each teacher is filtered to their own students.

**Account creation:**
- Admin-only. There's no self-signup. You (the first admin) create accounts for other teachers and hand them an initial password in person.
- Teachers are required to change the initial password on first login.

---

## Setup

### Requirements
- Python 3.10+
- Linux (Ubuntu/Debian), macOS, or Windows

### Install

```bash
# From inside the clockin folder
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure (recommended before going live)

Set a long random secret for session signing:

```bash
export CLOCKIN_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

On Linux, put this in `~/.bashrc` or in your systemd unit's `Environment=` lines.

### Run

```bash
python app.py
```

Output:
```
 * Running on http://0.0.0.0:5000
```

---

## First-run flow

1. From a classroom computer, open `http://your-server-ip:5000/login`.
2. The system detects there are no accounts yet and redirects you to **/setup**.
3. Enter your full name, a username, an optional email, and a password (at least 8 characters). This becomes the first admin account.
4. Log in normally with your username and password.
5. Upload your roster CSV from the **ROSTER** page. The 23 students from `sample_roster.csv` become yours.
6. Print badges from the **BADGES** page.
7. To add another teacher: **TEACHERS → + ADD TEACHER**. Give them their email and initial password in person. They'll be forced to change it on first login.

---



### Teacher courses

When you create the first admin account (and when admins create new teachers), there's an optional **COURSES YOU TEACH** field. Type the courses informally — `Cyber 1, CompE 2, IT Fund` — and the system normalizes them behind the scenes to canonical names (`Cybersecurity 1, Computer Engineering 2, IT Fundamentals`).

You can update your own courses any time from the **PROFILE** page. Admins can see every teacher's courses on the **TEACHERS** page.

Recognized variants for each course:
- **IT Fundamentals** — `IT Fund`, `ITF`, `Fundamentals`
- **Cybersecurity 1** — `Cyber 1`, `Cyber I`, `CYB1`, `Cybersecurity I`
- **Cybersecurity 2** — `Cyber 2`, `Cyber II`, `CYB2`
- **Computer Engineering 1** — `Comp Eng 1`, `CompE 1`, `CE1`
- **Computer Engineering 2** — `Comp Eng 2`, `CompE 2`, `CE2`

Anything else gets stored as you typed it.

## Daily workflow

### For students (the kiosk)
- The kiosk is always public at `http://your-server-ip:5000/`. No login.
- Scan badge → green CLOCKED IN screen.
- Scan again at end of period → blue CLOCKED OUT screen with shift duration.

### For teachers
- Sign in at `/login`.
- The **TODAY** page shows your students who clocked in, who didn't, and shift times.
- Click **EXPORT CSV** to get a file for entering into your school's official attendance system.

### For admins (you)
- All of the above, plus:
- On the dashboard and roster, toggle **VIEW: MY STUDENTS / ALL TEACHERS** to see other teachers' data.
- Manage teacher accounts from the **TEACHERS** page (create, promote, reset password, deactivate).

---

## Roster CSV format

**The fastest way:** go to the **ROSTER** page and click **EXAMPLE TEMPLATE**. You'll get a CSV with realistic sample rows and the columns already in the right order. Edit it in Excel or Google Sheets, save as CSV, upload.

If you want to start from scratch, click **BLANK TEMPLATE** instead — just the headers, no data.

### Auto-generated Employee IDs

Leave the `employee_id` column blank and the system fills it in based on the course:

| Course | Generated prefix |
|---|---|
| IT Fundamentals | `ITF-001`, `ITF-002`, `ITF-003`... |
| Cybersecurity 1 | `CYB1-001`, `CYB1-002`... |
| Cybersecurity 2 | `CYB2-001`, `CYB2-002`... |
| Computer Engineering 1 | `CE1-001`, `CE1-002`... |
| Computer Engineering 2 | `CE2-001`, `CE2-002`... |
| (anything else / blank) | `STU-001`, `STU-002`... |

Numbering continues from the highest existing ID, so re-uploading new students later won't collide.

If you want a specific ID (e.g. matching a school-issued number), just type it in the `employee_id` column and it'll be used as-is.

## Required columns:
- `employee_id` — the QR identifier (E001, E002, ...)
- `first_name`
- `last_name`
- `school`

Optional but recommended:
- `student_id` — your school's official student ID
- `role` — Help Desk Manager, Desktop Technician, etc.
- `course`
- `period`

**Conflict behavior:** if a CSV contains an `employee_id` already owned by another teacher, that row is skipped and you'll see a warning. You can ask the owning teacher to give them up, or (if you're an admin) reassign yourself.

---

## Password resets

There's no email server (yet). When a teacher forgets their password:

1. As admin, go to **TEACHERS**.
2. Click **RESET PASSWORD** next to their name.
3. A temporary password is shown in a flash message at the top of the screen.
4. **Copy it and give it to them in person** — it won't be shown again.
5. They log in with that temporary password and are forced to change it immediately.

Later, when we add email infrastructure, this will become a self-service "forgot password" link.

---

## Security notes for this version

What's secure:
- Passwords stored as bcrypt hashes (not plaintext, not reversible)
- Session cookies signed with `CLOCKIN_SECRET` — can't be forged without the secret
- Role-based access enforced on every protected route (not just the UI)
- One-admin-minimum guard prevents accidentally locking out all admins

What's intentionally simple for v1:
- No HTTPS by default. Fine for a firewalled classroom LAN, not okay if exposed to the public internet.
- No CSRF tokens on POST forms. Acceptable for a single-LAN tool; if you ever expose this beyond your school's network, add Flask-WTF.
- No rate limiting on login. A determined attacker on your LAN could brute-force a weak password. Use strong passwords.
- Sessions live 12 hours. Adjust `PERMANENT_SESSION_LIFETIME` in `app.py` if you want shorter.

---

## Migration from v1

If you ran v1 already and want to keep that data:
- v1's database had no `teachers` table and no `owner_teacher_id` column.
- The v2 schema is **additive only** — running `init_db()` on a v1 database will add the new tables but won't drop anything.
- However, your existing employees won't have an owner and won't show on any dashboard. The safest path is what you chose: **wipe and start fresh** with v2. Delete `data/clockin.db` and run setup again.

---

## When you're ready for what's next

Module 2: **Ticket Management Agent**. The `employees` table now has clean ownership, which means tickets can be scoped per-teacher (you see only your students' tickets) just like the roster.

— Ciri


Updated Mon May 18 01:48:40 PM EDT 2026
