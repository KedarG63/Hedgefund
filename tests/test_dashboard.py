"""
The dashboard is a thin view, but it has one structural requirement worth
pinning: it must import when run the way Streamlit runs it.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_app_imports_when_run_as_a_script():
    """
    Streamlit puts the SCRIPT's directory on sys.path, not the project root, so
    `from core...` raises ModuleNotFoundError -- and only once a browser session
    actually executes the script, NOT when the server starts. An HTTP 200 from
    the server proves nothing about this.

    Running the file directly reproduces Streamlit's sys.path exactly.
    """
    proc = subprocess.run(
        [sys.executable, str(REPO / "dashboard" / "app.py")],
        capture_output=True, text=True, timeout=300,
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr[-800:]
    assert "No module named 'core'" not in proc.stderr


def test_app_imports_from_an_unrelated_working_directory(tmp_path):
    """Launching from elsewhere must still find the repo's modules and data."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "dashboard" / "app.py")],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr[-800:]
