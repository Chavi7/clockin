"""The session-secret guard: a misconfigured deploy must not start silently.

Each case runs `import app` in a subprocess, because the guard fires at import
time and calls sys.exit(). Checking the real process is the only honest test.
"""
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACEHOLDER = "dev-secret-change-me-in-production"


def _import_app(**env_overrides):
    """Run `python -c "import app"` with a controlled environment."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLOCKIN_SECRET", "CLOCKIN_DATA_DIR")}
    env["CLOCKIN_DATA_DIR"] = tempfile.mkdtemp(prefix="clockin-secret-test-")
    env["PYTHONPATH"] = REPO_ROOT
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def test_absent_secret_still_boots_with_a_warning():
    """`python app.py` with nothing configured keeps working, loudly."""
    result = _import_app(CLOCKIN_SECRET=None)
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert "CLOCKIN_SECRET" in result.stderr


def test_blank_secret_refuses_to_start():
    """An empty string is what an unfilled compose variable becomes."""
    result = _import_app(CLOCKIN_SECRET="")
    assert result.returncode != 0
    assert "set but EMPTY" in result.stderr


def test_whitespace_only_secret_refuses_to_start():
    result = _import_app(CLOCKIN_SECRET="   ")
    assert result.returncode != 0
    assert "set but EMPTY" in result.stderr


def test_placeholder_secret_refuses_to_start():
    """The dev placeholder is public in this repo; it can't sign real sessions."""
    result = _import_app(CLOCKIN_SECRET=PLACEHOLDER)
    assert result.returncode != 0
    assert "built-in development" in result.stderr


def test_short_secret_refuses_to_start():
    result = _import_app(CLOCKIN_SECRET="too-short")
    assert result.returncode != 0
    assert "characters" in result.stderr


def test_valid_secret_boots_without_warning():
    result = _import_app(CLOCKIN_SECRET="a" * 32)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr
