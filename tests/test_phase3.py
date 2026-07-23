"""Phase 3 / A2 — entry points: pytest, and a hardened ``set_trace``.

* ``test_postmortem_over_websocket`` drives the *post-mortem* entry the way
  pytest does (``reset()`` then ``interaction(None, exc)``) and checks it over
  the real websocket — the debugger stops at the failing frame, reports the
  exception, and the in-frame console can inspect the crash's locals.
* ``test_pytest_pdbcls_lands_at_failure`` runs a *real* pytest subprocess with
  ``--pdb --pdbcls=judb:Debugger`` on a failing test and drives the resulting
  debugger over its websocket — the full "debug a failing test" loop.
* ``test_repeated_set_trace_reuses_one_server`` — several ``set_trace()`` calls
  in one run share a single server, hence a single browser tab.
* ``test_set_trace_on_last_line_pauses_in_user_code`` — regression test for
  tracing outliving the debuggee and stranding the browser in stdlib internals.
* ``test_python_m_judb_runs_script`` / ``…_runs_module`` — the ``python -m judb``
  entry point in both forms, as real subprocesses (which is the only way to
  catch the ``__main__``-namespace-clearing hazard the script form has).
"""

import asyncio
import os
import re
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientSession, ClientWebSocketResponse

import judb
from judb import Debugger


async def _recv_type(ws: ClientWebSocketResponse, want: str) -> dict[str, Any]:
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if msg.get("type") == want:
            return msg


def _ws_url(url: str) -> str:
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    return f"ws://{q.hostname}:{q.port}/ws?token={token}"


def _make_exception() -> BaseException:
    """Raise (and capture) an exception so we have a real traceback to inspect."""

    def inner() -> None:
        values = [10, 20, 30]  # noqa: F841 — inspected from the paused frame, not here
        raise ValueError("kaboom")

    try:
        inner()
    except ValueError as exc:
        return exc
    raise AssertionError("unreachable")


def test_postmortem_over_websocket():
    """pytest's post_mortem path: reset() + interaction(None, exc), over the wire."""
    dbg = Debugger()
    # Pre-start (headless) so we hold the URL; interaction's own post-mortem
    # start_server() is then an idempotent no-op.
    url = dbg.start_server(open_browser=False)
    exc = _make_exception()

    def post_mortem() -> None:
        # Exactly what _pytest.debugging.post_mortem does with our class.
        dbg.reset()
        dbg.interaction(None, exc)

    thread = threading.Thread(target=post_mortem)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(_ws_url(url)) as ws:
            paused = await _recv_type(ws, "paused")
            assert paused.get("postmortem") is True
            assert paused["exception"] == {"type": "ValueError", "message": "kaboom"}
            # Innermost (failing) frame is selected.
            assert paused["function"] == "inner"
            assert "values" in paused["locals"]

            # The console inspects the crashed frame's real locals.
            await ws.send_json({"cmd": "execute_cell", "code": "sum(values)"})
            result = await _recv_type(ws, "cell_result")
            assert result["success"]
            texts = [o["data"].get("text/plain") for o in result["outputs"]]
            assert "60" in texts

            # Resuming a post-mortem just leaves the debugger.
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def _read_judb_url(proc: subprocess.Popen[str], lines: list[str]) -> str:
    """Drain the subprocess output in a thread and return judb's UI URL.

    Draining continuously keeps the pipe from filling (which would block the
    paused subprocess once it resumes and writes its report).
    """
    found = threading.Event()
    box: dict[str, str] = {}

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            m = re.search(r"judb: debugger UI at (\S+)", line)
            if m and "url" not in box:
                box["url"] = m.group(1)
                found.set()

    threading.Thread(target=reader, daemon=True).start()
    if not found.wait(timeout=30):
        raise AssertionError("never saw judb URL. output:\n" + "".join(lines))
    return box["url"]


def test_pytest_pdbcls_lands_at_failure(tmp_path: Path):
    """A real `pytest --pdb --pdbcls=judb:Debugger` run, driven over the socket."""
    testfile = tmp_path / "test_boom_demo.py"
    testfile.write_text(
        textwrap.dedent(
            """
            def test_boom():
                values = [1, 2, 3]
                expected = 999
                assert sum(values) == expected
            """
        )
    )
    env = {**os.environ, "JUDB_NO_BROWSER": "1"}
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "pytest", str(testfile),
            "-s", "-p", "no:cacheprovider",
            "--pdb", "--pdbcls=judb:Debugger",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )  # fmt: skip
    lines: list[str] = []
    try:
        url = _read_judb_url(proc, lines)

        async def flow() -> dict[str, Any]:
            async with (
                ClientSession() as session,
                session.ws_connect(_ws_url(url)) as ws,
            ):
                paused = await _recv_type(ws, "paused")
                await ws.send_json({"cmd": "continue"})
                await _recv_type(ws, "running")
                return paused

        paused = asyncio.run(flow())
        assert paused.get("postmortem") is True
        assert paused["function"] == "test_boom"
        assert paused["exception"]["type"] == "AssertionError"
        assert "values" in paused["locals"]

        # After resume, the session finishes and reports the failure.
        assert proc.wait(timeout=30) != 0
        assert "1 failed" in "".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_repeated_set_trace_reuses_one_server():
    """Several `judb.set_trace()` calls in one run share one server (and tab)."""
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    server = dbg._server
    # Stand in for the module singleton so we drive the real judb.set_trace()
    # path without leaking a debugger into other tests.
    original = judb._active_debugger
    judb._active_debugger = dbg

    def debuggee() -> None:
        judb.set_trace(open_browser=False)
        first = 1  # noqa: F841 — pause #1 lands here
        judb.set_trace(open_browser=False)
        second = 2  # noqa: F841 — pause #2 lands here

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> tuple[dict[str, Any], dict[str, Any]]:
        async with ClientSession() as session, session.ws_connect(_ws_url(url)) as ws:
            paused_one = await _recv_type(ws, "paused")
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")
            paused_two = await _recv_type(ws, "paused")
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")
            return paused_one, paused_two

    try:
        paused_one, paused_two = asyncio.run(flow())
        thread.join(timeout=10)
        assert not thread.is_alive()
        # One server, one URL (so one browser tab), across both pauses.
        assert dbg._server is server
        assert dbg.start_server(open_browser=False) == url
        # Two genuinely distinct pauses, the second further down the function.
        assert paused_one["function"] == "debuggee"
        assert paused_two["function"] == "debuggee"
        assert paused_one["lineno"] < paused_two["lineno"]
    finally:
        judb._active_debugger = original


def test_set_trace_on_last_line_pauses_in_user_code(tmp_path: Path):
    """`set_trace()` as the final statement stops in the user's own frame.

    Regression test: tracing used to outlive the debuggee's code, so the next
    line event landed in `threading._shutdown` and the browser showed stdlib
    internals instead of the script.
    """
    script = tmp_path / "last_line_demo.py"
    script.write_text(
        textwrap.dedent(
            """
            import judb
            answer = 42
            judb.set_trace(open_browser=False)
            """
        )
    )
    env = {**os.environ, "JUDB_NO_BROWSER": "1"}
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    lines: list[str] = []
    try:
        url = _read_judb_url(proc, lines)

        async def flow() -> dict[str, Any]:
            async with (
                ClientSession() as session,
                session.ws_connect(_ws_url(url)) as ws,
            ):
                paused = await _recv_type(ws, "paused")
                await ws.send_json({"cmd": "continue"})
                await _recv_type(ws, "running")
                return paused

        paused = asyncio.run(flow())
        assert Path(paused["filename"]).name == "last_line_demo.py"
        assert paused["function"] == "<module>"
        assert paused.get("exiting") is True
        assert "answer" in paused["locals"]
        # Resuming from the exit stop ends tracing and the process exits cleanly
        # (rather than stepping onward into interpreter shutdown).
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


async def _pause_then_continue(url: str) -> dict[str, Any]:
    """Connect, capture the first `paused`, resume, and return that message."""
    async with ClientSession() as session, session.ws_connect(_ws_url(url)) as ws:
        paused = await _recv_type(ws, "paused")
        await ws.send_json({"cmd": "continue"})
        await _recv_type(ws, "running")
        return paused


def test_python_m_judb_runs_script(tmp_path: Path):
    """`python -m judb script.py a b` stops on entry, then runs to completion.

    Regression test: `python -m judb` executes judb/__main__.py *as* `__main__`,
    so clearing `__main__.__dict__` for the debuggee used to wipe that module's
    own globals mid-call (NameError on `__builtins__`/`Debugger`).
    """
    script = tmp_path / "argv_demo.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            seen = sys.argv[1:]
            print("ARGV:", seen)
            """
        ).lstrip()
    )
    env = {**os.environ, "JUDB_NO_BROWSER": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "judb", str(script), "one", "two"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    lines: list[str] = []
    try:
        paused = asyncio.run(_pause_then_continue(_read_judb_url(proc, lines)))
        assert Path(paused["filename"]).name == "argv_demo.py"
        assert paused["function"] == "<module>"
        assert paused["lineno"] == 1  # stop-on-entry: nothing has run yet
        assert proc.wait(timeout=30) == 0
        # The debuggee saw its own argv, not judb's.
        assert "ARGV: ['one', 'two']" in "".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_python_m_judb_runs_module(tmp_path: Path):
    """`python -m judb -m pkg.mod args` runs a module as __main__ under judb."""
    pkg = tmp_path / "judbdemo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(
        textwrap.dedent(
            """
            import sys
            marker = "module-entry"
            print("MODULE RAN", sys.argv[1:])
            """
        ).lstrip()
    )
    env = {**os.environ, "JUDB_NO_BROWSER": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "judb", "-m", "judbdemo.mod", "alpha"],
        cwd=tmp_path,  # `python -m` puts the cwd on sys.path, so judbdemo resolves
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    lines: list[str] = []
    try:
        paused = asyncio.run(_pause_then_continue(_read_judb_url(proc, lines)))
        assert Path(paused["filename"]).name == "mod.py"
        assert paused["function"] == "<module>"
        assert proc.wait(timeout=30) == 0
        assert "MODULE RAN ['alpha']" in "".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()
