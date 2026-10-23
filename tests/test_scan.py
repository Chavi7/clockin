"""The kiosk scan path: clock in, clock out, and the failure cases."""
from helpers import GOOD_ROSTER, badge_json, upload_roster


def _scan(client, raw):
    return client.post("/api/scan", json={"raw": raw})


def test_scan_a_badge_clocks_in_then_out(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees ORDER BY id").fetchone()["employee_id"]

    first = _scan(admin_client, badge_json(employee_id))
    assert first.status_code == 200
    body = first.get_json()
    assert body["ok"] is True
    assert body["action"] == "clock_in"
    assert body["name"] == "Alex Rivera"

    second = _scan(admin_client, badge_json(employee_id))
    assert second.status_code == 200
    assert second.get_json()["action"] == "clock_out"

    shift = db.execute("SELECT * FROM shifts WHERE employee_id = ?",
                       (employee_id,)).fetchone()
    assert shift["clock_in_at"] is not None
    assert shift["clock_out_at"] is not None


def test_scan_accepts_a_typed_employee_id(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees ORDER BY id").fetchone()["employee_id"]

    # No JSON, no QR — the manual-input fallback on the kiosk.
    response = _scan(admin_client, employee_id.lower())
    assert response.status_code == 200
    assert response.get_json()["action"] == "clock_in"


def test_a_third_scan_is_rejected(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees ORDER BY id").fetchone()["employee_id"]

    _scan(admin_client, badge_json(employee_id))
    _scan(admin_client, badge_json(employee_id))
    third = _scan(admin_client, badge_json(employee_id))
    assert third.status_code == 409
    assert third.get_json()["ok"] is False
    assert "already clocked out" in third.get_json()["error"]


def test_unknown_badge_is_a_404(admin_client):
    response = _scan(admin_client, badge_json("NOT-A-REAL-ID"))
    assert response.status_code == 404
    assert response.get_json()["ok"] is False


def test_empty_scan_is_a_400(admin_client):
    response = admin_client.post("/api/scan", json={})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_deactivated_employee_cannot_clock_in(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    row = db.execute("SELECT * FROM employees ORDER BY id").fetchone()
    admin_client.post(f"/roster/{row['id']}/toggle", follow_redirects=False)

    response = _scan(admin_client, badge_json(row["employee_id"]))
    assert response.status_code == 404


def test_clock_out_records_a_duration(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees ORDER BY id").fetchone()["employee_id"]
    _scan(admin_client, badge_json(employee_id))
    second = _scan(admin_client, badge_json(employee_id))
    assert second.get_json()["duration"] is not None
    assert "m" in second.get_json()["duration"]


def test_scan_exposes_no_student_data_beyond_the_kiosk_screen(admin_client, db):
    """The kiosk is public — it must not leak a full employee row."""
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees ORDER BY id").fetchone()["employee_id"]
    body = _scan(admin_client, badge_json(employee_id)).get_json()
    assert set(body) <= {"ok", "action", "name", "role", "period", "time", "duration"}
