"""
The dashboard is a thin view, but it has one structural requirement worth
pinning: it must import when run the way Streamlit runs it.
"""
import ast
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_query_goes_through_a_cursor_not_the_shared_connection():
    """
    @st.cache_resource hands ONE DuckDBPyConnection to every browser session and
    every rerun thread. execute() parks its result on the connection and df()
    then fetches it, so interleaved calls race: one thread's df() collects the
    other's result and the loser gets None. It does not raise -- None just flows
    into callers that assume a DataFrame, and the page dies somewhere unrelated
    with "'NoneType' object has no attribute 'empty'".

    Asserted on the source rather than by racing threads, because a timing test
    for this passes whenever the race happens not to trigger.
    """
    tree = ast.parse((REPO / "dashboard" / "app.py").read_text(encoding="utf-8"))
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "query"), None)
    assert fn is not None, "dashboard/app.py no longer defines query()"

    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "cursor" in attrs, (
        "query() must call con.cursor().execute(...), not con.execute(...): the "
        "cached connection is shared across threads and the result set is held "
        "on the connection itself."
    )


def test_duckdb_cursor_is_concurrency_safe_for_reads():
    """The property the fix relies on: cursors over one database serve
    concurrent readers without dropping results."""
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 5.25 AS rate_pct")

    nones, errors = [], []
    lock = threading.Lock()

    def worker():
        for _ in range(40):
            try:
                df = con.cursor().execute("SELECT rate_pct FROM t").df()
                if df is None:
                    with lock:
                        nones.append(1)
            except Exception as exc:                        # noqa: BLE001
                with lock:
                    errors.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not nones, f"{len(nones)} calls returned None"
    assert not errors, f"errors: {set(errors)}"


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
    # A bad key=/on_change= wiring on a cross-linked widget (the persistent
    # focus-symbol selector and its per-tab drill-down selectboxes) raises
    # one of these at import time even in bare mode, well before a browser
    # ever opens the page.
    assert "KeyError" not in proc.stderr, proc.stderr[-800:]
    assert "StreamlitAPIException" not in proc.stderr, proc.stderr[-800:]


def test_app_imports_from_an_unrelated_working_directory(tmp_path):
    """Launching from elsewhere must still find the repo's modules and data."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "dashboard" / "app.py")],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
    )
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr[-800:]
    assert "KeyError" not in proc.stderr, proc.stderr[-800:]
    assert "StreamlitAPIException" not in proc.stderr, proc.stderr[-800:]
