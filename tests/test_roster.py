"""Roster CSV import, export, and the employee-ID generation rules."""
from helpers import GOOD_ROSTER, upload_roster


def test_import_splits_a_single_name_column_and_generates_ids(admin_client, db):
    response = upload_roster(admin_client, GOOD_ROSTER)
    assert response.status_code == 302

    rows = db.execute("SELECT * FROM employees ORDER BY id").fetchall()
    assert len(rows) == 2

    alex = rows[0]
    assert alex["first_name"] == "Alex"
    assert alex["last_name"] == "Rivera"
    # Cybersecurity 1 -> CYB1 prefix
    assert alex["employee_id"].startswith("CYB1-")
    assert alex["period"] == "A.M."

    maria = rows[1]
    # Multi-word surname must survive the split.
    assert maria["first_name"] == "Maria"
    assert maria["last_name"] == "De La Cruz"
    assert maria["employee_id"].startswith("CE2-")
    assert maria["period"] == "P.M."


def test_import_keeps_an_explicit_employee_id(admin_client, db):
    upload_roster(admin_client,
                  "name,school,course,employee_id\n"
                  "Sam Okafor,CTEC,Cybersecurity 2,CUSTOM-042\n")
    row = db.execute("SELECT * FROM employees").fetchone()
    assert row["employee_id"] == "CUSTOM-042"


def test_import_accepts_the_legacy_split_name_columns(admin_client, db):
    upload_roster(admin_client,
                  "first_name,last_name,school,course\n"
                  "Jordan,Lee,CTEC,IT Fundamentals\n")
    row = db.execute("SELECT * FROM employees").fetchone()
    assert row["first_name"] == "Jordan"
    assert row["last_name"] == "Lee"
    assert row["employee_id"].startswith("ITF-")


def test_import_requires_a_name_column(admin_client, db):
    upload_roster(admin_client, "school,course\nCTEC,IT Fundamentals\n")
    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0


def test_import_requires_the_school_column(admin_client, db):
    upload_roster(admin_client, "name,course\nAlex Rivera,IT Fundamentals\n")
    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0


def test_period_variants_are_normalized(admin_client, db):
    upload_roster(admin_client,
                  "name,school,course,period\n"
                  "Morning Kid,CTEC,IT Fundamentals,am\n"
                  "Afternoon Kid,CTEC,IT Fundamentals,afternoon\n"
                  "Nonsense Kid,CTEC,IT Fundamentals,lunch\n")
    periods = {r["first_name"]: r["period"]
               for r in db.execute("SELECT * FROM employees").fetchall()}
    assert periods["Morning"] == "A.M."
    assert periods["Afternoon"] == "P.M."
    assert periods["Nonsense"] in (None, "")


def test_reuploading_the_same_employee_id_updates_in_place(admin_client, db):
    """Matching is by employee_id, so an explicit ID updates instead of adding."""
    upload_roster(admin_client,
                  "name,school,course,employee_id\n"
                  "Alex Rivera,CTEC,Cybersecurity 1,CYB1-001\n")
    upload_roster(admin_client,
                  "name,school,course,employee_id\n"
                  "Alexandra Rivera,CTEC,Cybersecurity 1,CYB1-001\n")

    rows = db.execute("SELECT * FROM employees").fetchall()
    assert len(rows) == 1
    assert rows[0]["first_name"] == "Alexandra"


def test_reuploading_the_same_roster_does_not_duplicate_students(admin_client, db):
    """Re-uploading a roster with no employee_id column updates instead of adding.

    Matching is by student_id first, so the same CSV imported twice leaves the
    roster at two students with their original employee IDs.
    """
    upload_roster(admin_client, GOOD_ROSTER)
    original_ids = [r["employee_id"] for r in
                    db.execute("SELECT employee_id FROM employees ORDER BY id")]

    upload_roster(admin_client, GOOD_ROSTER)

    rows = db.execute("SELECT * FROM employees ORDER BY id").fetchall()
    assert len(rows) == 2
    assert [r["employee_id"] for r in rows] == original_ids


def test_a_corrected_name_updates_the_existing_student(admin_client, db):
    """A typo fix on a known student_id must not create a second student."""
    upload_roster(admin_client,
                  "name,student_id,school,course\n"
                  "Alx Rivera,1001,CTEC,Cybersecurity 1\n")
    upload_roster(admin_client,
                  "name,student_id,school,course\n"
                  "Alex Rivera,1001,CTEC,Cybersecurity 1\n")

    rows = db.execute("SELECT * FROM employees").fetchall()
    assert len(rows) == 1
    assert rows[0]["first_name"] == "Alex"


def test_without_a_student_id_the_match_falls_back_to_name_and_course(admin_client, db):
    upload_roster(admin_client,
                  "name,school,course\nAlex Rivera,CTEC,Cybersecurity 1\n")
    upload_roster(admin_client,
                  "name,school,course\nAlex Rivera,CTEC,Cybersecurity 1\n")

    rows = db.execute("SELECT * FROM employees").fetchall()
    assert len(rows) == 1


def test_same_name_in_a_different_course_is_a_different_student(admin_client, db):
    upload_roster(admin_client,
                  "name,school,course\nAlex Rivera,CTEC,Cybersecurity 1\n")
    upload_roster(admin_client,
                  "name,school,course\nAlex Rivera,CTEC,IT Fundamentals\n")

    rows = db.execute("SELECT * FROM employees").fetchall()
    assert len(rows) == 2
    assert {r["course"] for r in rows} == {"Cybersecurity 1", "IT Fundamentals"}


def test_an_ambiguous_match_is_skipped_rather_than_guessed(admin_client, db):
    """Two students with the same name in the same course: refuse to pick one."""
    upload_roster(admin_client,
                  "name,school,course,employee_id\n"
                  "Alex Rivera,CTEC,Cybersecurity 1,CYB1-001\n"
                  "Alex Rivera,CTEC,Cybersecurity 1,CYB1-002\n")
    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 2

    # No employee_id, no student_id -> two candidates -> skipped, not duplicated.
    upload_roster(admin_client,
                  "name,school,course\nAlex Rivera,CTEC,Cybersecurity 1\n")

    assert db.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 2
    page = admin_client.get("/roster")
    assert b"matched more than one existing student" in page.data


def test_the_import_summary_reports_matched_rows(admin_client):
    upload_roster(admin_client, GOOD_ROSTER)
    response = upload_roster(admin_client, GOOD_ROSTER)
    page = admin_client.get(response.headers["Location"])
    assert b"matched an existing student" in page.data


def test_employee_ids_do_not_collide_across_uploads(admin_client, db):
    upload_roster(admin_client,
                  "name,school,course\nFirst Student,CTEC,IT Fundamentals\n")
    upload_roster(admin_client,
                  "name,school,course\nSecond Student,CTEC,IT Fundamentals\n")
    ids = [r["employee_id"] for r in db.execute("SELECT * FROM employees").fetchall()]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_roster_export_round_trips_the_imported_data(admin_client):
    upload_roster(admin_client, GOOD_ROSTER)
    response = admin_client.get("/roster/export.csv")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Alex" in body
    assert "Rivera" in body
    assert "De La Cruz" in body


def test_blank_and_example_templates_download(admin_client):
    blank = admin_client.get("/roster/template/blank.csv")
    example = admin_client.get("/roster/template/example.csv")
    assert blank.status_code == 200
    assert example.status_code == 200
    assert "name" in blank.data.decode("utf-8").lower()
