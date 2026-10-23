"""Shared fixtures for the CLOCKIN test suite.

`app.py` reads its environment and creates its data directory at *import* time,
so the environment has to be right before the import below — hence the
top-level setup rather than a fixture.
"""
import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A throwaway data directory, and a real (non-placeholder) secret so importing
# the module doesn't emit the development-secret warning.
os.environ["CLOCKIN_DATA_DIR"] = tempfile.mkdtemp(prefix="clockin-tests-")
os.environ["CLOCKIN_SECRET"] = "test-only-secret-value-not-a-deployment-key"

import app as clockin  # noqa: E402

ADMIN_USERNAME = "teacher"
ADMIN_PASSWORD = "teacher-password-123"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A test client backed by a fresh, empty database."""
    monkeypatch.setattr(clockin, "DB_PATH", tmp_path / "clockin.db")
    clockin.init_db()
    clockin.app.config.update(TESTING=True)
    return clockin.app.test_client()


@pytest.fixture()
def admin_client(client):
    """A test client already logged in as the first admin."""
    client.post("/setup", data={
        "full_name": "Test Teacher",
        "username": ADMIN_USERNAME,
        "email": "",
        "courses": "Cyber 1, CompE 2",
        "password": ADMIN_PASSWORD,
        "confirm": ADMIN_PASSWORD,
    }, follow_redirects=False)
    client.post("/login",
                data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                follow_redirects=False)
    return client


@pytest.fixture()
def db(tmp_path, client):
    """A direct sqlite connection to the same database the client is using."""
    import sqlite3
    conn = sqlite3.connect(clockin.DB_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
