"""Phase 3 / A2 — the pytest entry point (``--pdbcls=judb:Debugger``).

Two levels:

* ``test_postmortem_over_websocket`` drives the *post-mortem* entry the way
  pytest does (``reset()`` then ``interaction(None, exc)``) and checks it over
  the real websocket — the debugger stops at the failing frame, reports the
  exception, and the in-frame console can inspect the crash's locals.
* ``test_pytest_pdbcls_lands_at_failure`` runs a *real* pytest subprocess with
  ``--pdb --pdbcls=judb:Debugger`` on a failing test and drives the resulting
  debugger over its websocket — the full "debug a failing test" loop.
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
