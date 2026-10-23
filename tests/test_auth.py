"""Auth, first-run setup, and role enforcement."""
from conftest import ADMIN_USERNAME


def test_login_redirects_to_setup_on_a_fresh_database(client):
    response = client.get("/login")
    assert response.status_code == 302
    assert "/setup" in response.headers["Location"]


def test_setup_creates_the_first_admin(client, db):
    response = client.post("/setup", data={
        "full_name": "Test Teacher",
        "username": "tester",
        "email": "tester@example.com",
        "courses": "Cyber 1",
        "password": "supersecret123",
        "confirm": "supersecret123",
    }, follow_redirects=False)

    assert response.status_code == 302
    row = db.execute("SELECT * FROM teachers").fetchone()
    assert row["username"] == "tester"
    assert row["role"] == "admin"
    # Never store the plaintext password.
    assert row["password_hash"] != "supersecret123"
    assert row["password_hash"].startswith("$2")


def test_setup_is_closed_once_an_admin_exists(admin_client):
    response = admin_client.get("/setup")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_setup_rejects_mismatched_password_confirmation(client, db):
    client.post("/setup", data={
        "full_name": "Test Teacher",
        "username": "tester",
        "email": "",
        "courses": "",
        "password": "supersecret123",
        "confirm": "different12345",
    })
    assert db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0] == 0


def test_setup_rejects_a_short_password(client, db):
    client.post("/setup", data={
        "full_name": "Test Teacher",
        "username": "tester",
        "email": "",
        "courses": "",
        "password": "short",
        "confirm": "short",
    })
    assert db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0] == 0


def test_login_rejects_a_wrong_password(client, admin_client):
    client.get("/logout")
    response = client.post("/login",
                           data={"username": ADMIN_USERNAME, "password": "wrong-password"},
                           follow_redirects=False)
    # Re-renders the login form (200) and leaves the session unauthenticated.
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data
    assert client.get("/dashboard").status_code == 302


def test_login_accepts_the_right_password(admin_client):
    # admin_client is already logged in by the fixture; prove it can see data.
    assert admin_client.get("/dashboard").status_code == 200


def test_protected_pages_redirect_when_not_logged_in(client):
    for path in ("/dashboard", "/roster", "/teachers", "/badges"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"], path


def test_kiosk_is_public(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"SCAN" in response.data.upper()


def test_teacher_accounts_cannot_reach_admin_pages(admin_client, client):
    # Admin creates a second, non-admin teacher.
    admin_client.post("/teachers/new", data={
        "full_name": "Second Teacher",
        "username": "second",
        "email": "",
        "password": "secondpassword123",
        "role": "teacher",
        "courses": "IT Fundamentals",
    }, follow_redirects=False)

    # That teacher logs in (and is pushed to change the initial password).
    other = client.application.test_client()
    other.post("/login", data={"username": "second", "password": "secondpassword123"},
               follow_redirects=False)

    response = other.get("/teachers")
    assert response.status_code == 403


def test_first_login_with_a_temporary_password_forces_a_reset(admin_client, client):
    admin_client.post("/teachers/new", data={
        "full_name": "Second Teacher",
        "username": "second",
        "email": "",
        "password": "secondpassword123",
        "role": "teacher",
        "courses": "",
    }, follow_redirects=False)

    other = client.application.test_client()
    other.post("/login", data={"username": "second", "password": "secondpassword123"},
               follow_redirects=False)

    # must_reset is enforced in login_required: every page bounces to /profile.
    response = other.get("/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "/profile" in response.headers["Location"]


def test_logout_clears_the_session(admin_client):
    """The logout route is GET-only, which is worth knowing before you POST it."""
    admin_client.get("/logout")
    response = admin_client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
