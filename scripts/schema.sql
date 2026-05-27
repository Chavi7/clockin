-- CLOCKIN v2 schema. Multi-teacher, role-based.
-- Idempotent — uses CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS teachers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    UNIQUE NOT NULL,            -- login identifier (lowercased)
    email           TEXT,                                -- optional, for future password reset
    password_hash   TEXT    NOT NULL,
    full_name       TEXT    NOT NULL,
    role            TEXT    NOT NULL DEFAULT 'teacher',  -- 'admin' | 'teacher'
    courses         TEXT    NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    must_reset      INTEGER NOT NULL DEFAULT 0,
    reset_token     TEXT,
    reset_expires   TEXT,
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    last_login_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_teachers_username ON teachers(username);
CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers(email);

CREATE TABLE IF NOT EXISTS employees (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id       TEXT    UNIQUE NOT NULL,
    school            TEXT    NOT NULL,
    first_name        TEXT    NOT NULL,
    last_name         TEXT    NOT NULL,
    student_id        TEXT,
    school_year       TEXT,
    role              TEXT,
    course            TEXT,
    period            TEXT,
    owner_teacher_id  INTEGER NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_teacher_id) REFERENCES teachers(id)
);

CREATE INDEX IF NOT EXISTS idx_employees_active ON employees(active);
CREATE INDEX IF NOT EXISTS idx_employees_period ON employees(period);
CREATE INDEX IF NOT EXISTS idx_employees_owner  ON employees(owner_teacher_id);

CREATE TABLE IF NOT EXISTS shifts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id     TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    clock_in_at     TEXT,
    clock_out_at    TEXT,
    period          TEXT,
    notes           TEXT,
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id),
    UNIQUE (employee_id, date)
);

CREATE INDEX IF NOT EXISTS idx_shifts_date     ON shifts(date);
CREATE INDEX IF NOT EXISTS idx_shifts_employee ON shifts(employee_id);

CREATE TABLE IF NOT EXISTS role_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id     TEXT    NOT NULL,
    old_role        TEXT,                          -- previous role (may be empty)
    new_role        TEXT,                          -- new role (may be empty if unset)
    changed_by      INTEGER NOT NULL,              -- teacher who made the change
    changed_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    note            TEXT,                          -- optional reason / comment
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id),
    FOREIGN KEY (changed_by)  REFERENCES teachers(id)
);

CREATE INDEX IF NOT EXISTS idx_role_history_employee ON role_history(employee_id);
CREATE INDEX IF NOT EXISTS idx_role_history_at       ON role_history(changed_at);
