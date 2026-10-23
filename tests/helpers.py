"""Small helpers shared by the test modules."""
import io


def upload_roster(client, csv_text, filename="roster.csv"):
    """POST a CSV to /roster/upload the way the browser form does."""
    return client.post(
        "/roster/upload",
        data={"roster": (io.BytesIO(csv_text.encode("utf-8")), filename)},
        content_type="multipart/form-data",
        follow_redirects=False,
    )


GOOD_ROSTER = (
    "name,student_id,school_year,school,role,course,period\n"
    "Alex Rivera,1001,2026-2027,CTEC,Help Desk Manager,Cybersecurity 1,A.M.\n"
    "Maria De La Cruz,1002,2026-2027,CTEC,Desktop Technician,"
    "Computer Engineering 2,P.M.\n"
)


def badge_json(employee_id, name="Alex Rivera", school="CTEC", student_id="1001"):
    """The compact JSON a CLOCKIN badge QR carries (matches draw_badge())."""
    import json
    return json.dumps({
        "school": school,
        "name": name,
        "employee_id": employee_id,
        "student_id": student_id,
    }, separators=(",", ":"))
