"""PDF badges, CSV exports, and the dashboard views."""
from helpers import GOOD_ROSTER, badge_json, upload_roster


def test_badge_pdf_is_a_real_pdf(admin_client):
    upload_roster(admin_client, GOOD_ROSTER)
    response = admin_client.get("/badges.pdf")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert len(response.data) > 1000


def test_badge_pdf_with_no_students_redirects(admin_client):
    response = admin_client.get("/badges.pdf", follow_redirects=False)
    assert response.status_code == 302


def test_badge_pdf_can_be_filtered_by_period(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    both = admin_client.get("/badges.pdf").data
    morning_only = admin_client.get("/badges.pdf?period=A.M.").data
    assert len(morning_only) < len(both)


def test_dashboard_renders_students_after_import(admin_client):
    upload_roster(admin_client, GOOD_ROSTER)
    response = admin_client.get("/dashboard")
    assert response.status_code == 200
    assert b"Rivera" in response.data


def test_dashboard_shows_a_student_after_a_scan(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees").fetchone()["employee_id"]
    admin_client.post("/api/scan", json={"raw": badge_json(employee_id)})
    response = admin_client.get("/dashboard")
    assert b"Alex" in response.data


def test_dashboard_csv_export_has_the_clocked_in_student(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    employee_id = db.execute("SELECT employee_id FROM employees").fetchone()["employee_id"]
    admin_client.post("/api/scan", json={"raw": badge_json(employee_id)})

    response = admin_client.get("/dashboard/export.csv")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Alex" in body
    assert employee_id in body


def test_dashboard_csv_export_is_empty_before_any_scan(admin_client):
    upload_roster(admin_client, GOOD_ROSTER)
    response = admin_client.get("/dashboard/export.csv")
    assert response.status_code == 200


def test_employee_history_page_records_a_role_change(admin_client, db):
    upload_roster(admin_client, GOOD_ROSTER)
    row = db.execute("SELECT * FROM employees").fetchone()

    admin_client.post(f"/roster/{row['id']}/edit", data={
        "role": "Shift Supervisor",
        "period": "A.M.",
        "course": row["course"] or "",
        "note": "Promoted for excellent help desk performance",
    }, follow_redirects=False)

    history = admin_client.get(f"/roster/{row['id']}/history")
    assert history.status_code == 200
    assert b"Shift Supervisor" in history.data

    updated = db.execute("SELECT role FROM employees WHERE id = ?", (row["id"],)).fetchone()
    assert updated["role"] == "Shift Supervisor"
