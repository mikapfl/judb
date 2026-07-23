"""Phase 2 protocol additions, verified over the real websocket without a browser.

`select_frame` retargets the console + inspection to a chosen stack frame: a cell
that fails in the innermost frame (a name defined only in an outer frame) succeeds
after selecting that outer frame.
"""

import asyncio
import os
import pty
import re
import signal
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import ClientSession, ClientWebSocketResponse

from judb import Debugger


async def _recv_type(ws: ClientWebSocketResponse, want: str) -> dict[str, Any]:
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if msg.get("type") == want:
            return msg


def _ws_url(url: str) -> str:
    q = urlparse(url)
    return f"ws://{q.hostname}:{q.port}/ws?token={parse_qs(q.query)['token'][0]}"


def _has_error(outputs: list[dict[str, Any]], ename: str) -> bool:
    return any(
        o["kind"] == "error" and o["data"].get("ename") == ename for o in outputs
    )


def test_select_frame_retargets_console():
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def inner() -> None:
        dbg.set_trace()  # innermost frame; `marker` is NOT visible here
        _ = 1

    def outer() -> None:
        marker = "OUTER_LOCAL"  # noqa: F841 — read from the browser via the frame
        inner()

    thread = threading.Thread(target=outer)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            paused = await _recv_type(ws, "paused")
            stack = paused["stack"]
            # Selection starts at the innermost frame.
            assert paused["selected"] == len(stack) - 1
            assert stack[paused["selected"]]["function"] == "inner"
            outer_idx = next(i for i, f in enumerate(stack) if f["function"] == "outer")

            # In the innermost frame, `marker` is undefined.
            await ws.send_json({"cmd": "execute_cell", "code": "marker"})
            r1 = await _recv_type(ws, "cell_result")
            assert _has_error(r1["outputs"], "NameError")

            # Retarget to the outer frame.
            await ws.send_json({"cmd": "select_frame", "index": outer_idx})
            sel = await _recv_type(ws, "frame_selected")
            assert sel["index"] == outer_idx
            assert sel["function"] == "outer"
            assert "marker" in sel["locals"]

            # Now the same cell resolves `marker` from the selected frame.
            await ws.send_json({"cmd": "execute_cell", "code": "marker"})
            r2 = await _recv_type(ws, "cell_result")
            texts = [o["data"].get("text/plain", "") for o in r2["outputs"]]
            assert any("OUTER_LOCAL" in t for t in texts)

            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_local_does_not_leak_across_frames():
    """Regression: a name run in the inner frame must not linger in the console
    namespace when a later cell targets an outer frame that lacks it."""
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def inner() -> None:
        secret = "INNER_ONLY"
        dbg.set_trace()
        _ = secret

    def outer() -> None:
        inner()

    thread = threading.Thread(target=outer)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            paused = await _recv_type(ws, "paused")
            stack = paused["stack"]
            outer_idx = next(i for i, f in enumerate(stack) if f["function"] == "outer")

            # Resolvable in the inner (selected) frame...
            await ws.send_json({"cmd": "execute_cell", "code": "secret"})
            r1 = await _recv_type(ws, "cell_result")
            assert any(
                "INNER_ONLY" in o["data"].get("text/plain", "") for o in r1["outputs"]
            )

            # ...but must NOT leak into the outer frame.
            await ws.send_json({"cmd": "select_frame", "index": outer_idx})
            await _recv_type(ws, "frame_selected")
            await ws.send_json({"cmd": "execute_cell", "code": "secret"})
            r2 = await _recv_type(ws, "cell_result")
            assert _has_error(r2["outputs"], "NameError")

            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_complete_uses_selected_frame_namespace():
    """`complete` returns full replacements for the fragment before the cursor,
    drawn from the paused frame's locals — including after retargeting a frame."""
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def inner() -> None:
        dbg.set_trace()
        _ = 1

    def outer() -> None:
        marmalade = 42  # noqa: F841 — completed from the browser via the frame
        inner()

    thread = threading.Thread(target=outer)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            paused = await _recv_type(ws, "paused")
            stack = paused["stack"]
            outer_idx = next(i for i, f in enumerate(stack) if f["function"] == "outer")

            # `marmalade` lives only in the outer frame; select it first.
            await ws.send_json({"cmd": "select_frame", "index": outer_idx})
            await _recv_type(ws, "frame_selected")

            code = "marm"
            await ws.send_json({"cmd": "complete", "code": code, "cursor": len(code)})
            comp = await _recv_type(ws, "completions")
            assert "marmalade" in comp["matches"]
            # `from` marks where the replaced fragment ("marm") begins.
            assert comp["from"] == 0

            # Attribute completion on a real object descends into it.
            await ws.send_json({"cmd": "execute_cell", "code": "s = 'abc'"})
            await _recv_type(ws, "cell_result")
            code = "s.upp"
            await ws.send_json({"cmd": "complete", "code": code, "cursor": len(code)})
            comp = await _recv_type(ws, "completions")
            assert any(m.endswith("upper") for m in comp["matches"])
            # The fragment replaced is ".upp", starting right after `s`.
            assert code[comp["from"] :] == ".upp"

            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_expand_returns_repr_and_children():
    """`expand` resolves a variable path in the selected frame and returns a mime
    bundle for the value plus one level of navigable children."""
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        data = {"alpha": [10, 20], "beta": "hi"}  # noqa: F841 — inspected via the frame
        dbg.set_trace()
        _ = 1

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            await _recv_type(ws, "paused")

            # Expand the top-level dict.
            await ws.send_json({"cmd": "expand", "path": [["name", "data"]]})
            exp = await _recv_type(ws, "expanded")
            assert "error" not in exp
            assert "text/plain" in exp["repr"]
            keys = {c["key"]: c for c in exp["children"]}
            assert set(keys) == {"'alpha'", "'beta'"}
            assert keys["'alpha'"]["expandable"] is True  # a non-empty list
            assert keys["'beta'"]["expandable"] is False  # a str is a leaf

            # Descend into the nested list using the child's echoed path.
            await ws.send_json({"cmd": "expand", "path": keys["'alpha'"]["path"]})
            nested = await _recv_type(ws, "expanded")
            assert [c["key"] for c in nested["children"]] == ["0", "1"]
            assert "20" in nested["children"][1]["summary"]

            # A bad path surfaces as an error node, not a crash.
            await ws.send_json({"cmd": "expand", "path": [["name", "nonexistent"]]})
            bad = await _recv_type(ws, "expanded")
            assert "error" in bad

            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_set_break_stops_at_a_later_line():
    """A breakpoint set from the gutter while paused fires on the next
    ``continue``, re-pausing the debuggee at that line."""
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        dbg.set_trace()  # first pause lands on the next line (`a = 1`)
        a = 1
        b = 2
        c = 3
        _ = (a, b, c)

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            paused = await _recv_type(ws, "paused")
            fname = paused["filename"]
            assert paused["breakpoints"] == []  # none set yet
            target = paused["lineno"] + 2  # two lines on: `c = 3`

            await ws.send_json({"cmd": "set_break", "filename": fname, "line": target})
            bp = await _recv_type(ws, "breakpoints")
            assert bp["lines"] == [target]
            assert "error" not in bp

            # Continue: bdb keeps tracing while a breakpoint exists, so we stop.
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")
            paused2 = await _recv_type(ws, "paused")
            assert paused2["lineno"] == target
            assert paused2["breakpoints"] == [target]  # echoed on pause too

            # Clear it and run to completion.
            await ws.send_json(
                {"cmd": "clear_break", "filename": fname, "line": target}
            )
            cleared = await _recv_type(ws, "breakpoints")
            assert cleared["lines"] == []
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_set_break_on_nonexistent_line_reports_error():
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        dbg.set_trace()
        _ = 1

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            paused = await _recv_type(ws, "paused")
            # A line far past the file's end has no code: bdb rejects it.
            await ws.send_json(
                {
                    "cmd": "set_break",
                    "filename": paused["filename"],
                    "line": 10_000_000,
                }
            )
            bp = await _recv_type(ws, "breakpoints")
            assert "error" in bp
            assert bp["lines"] == []  # nothing was set
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_interrupt_stops_a_runaway_cell():
    """A cell stuck in an infinite Python loop is stopped by ``interrupt``,
    which raises KeyboardInterrupt into the debuggee thread; the cell comes back
    as an error and the debugger is still usable afterwards."""
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        dbg.set_trace()
        _ = 1

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            await _recv_type(ws, "paused")

            # Launch a runaway cell, let it spin, then interrupt it.
            await ws.send_json({"cmd": "execute_cell", "code": "while True:\n    pass"})
            await asyncio.sleep(0.5)
            await ws.send_json({"cmd": "interrupt"})
            result = await _recv_type(ws, "cell_result")
            assert _has_error(result["outputs"], "KeyboardInterrupt")

            # The console still works after the interrupt.
            await ws.send_json({"cmd": "execute_cell", "code": "1 + 1"})
            again = await _recv_type(ws, "cell_result")
            texts = [o["data"].get("text/plain", "") for o in again["outputs"]]
            assert any("2" in t for t in texts)

            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def _read_pty_for_url(master: int, sink: list[str], timeout: float = 30) -> str:
    """Drain the pty master on a thread and return judb's UI URL.

    Draining continuously keeps the tty buffer from filling while the debuggee
    is paused, and keeps collecting the traceback it prints on the way out.
    """
    found = threading.Event()
    box: dict[str, str] = {}

    def reader() -> None:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # EIO once the child is gone
                return
            if not chunk:
                return
            sink.append(chunk.decode(errors="replace"))
            match = re.search(r"judb: debugger UI at (\S+)", "".join(sink))
            if match and "url" not in box:
                box["url"] = match.group(1)
                found.set()

    threading.Thread(target=reader, daemon=True).start()
    if not found.wait(timeout=timeout):
        raise AssertionError("never saw judb URL. output:\n" + "".join(sink))
    return box["url"]


def _wait_for_exit(pid: int, timeout: float) -> int | None:
    """waitpid with a deadline; None if the child is still running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return status
        time.sleep(0.05)
    return None


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX controlling tty")
# pty.fork warns about forking a multi-threaded process; we exec immediately in
# the child, so none of the inherited state it worries about is ever touched.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded")
def test_terminal_ctrl_c_while_paused_ends_the_debuggee(tmp_path: Path):
    """End to end: Ctrl+C in the *controlling terminal* ends a paused debuggee.

    The debuggee gets a pty as its controlling terminal (``pty.fork`` does the
    ``setsid`` + ``TIOCSCTTY`` dance that merely inheriting a slave fd does not),
    runs until it is genuinely paused — we wait for a ``paused`` message over the
    websocket, so the UI server is up *and* the interaction loop is sitting in
    its idle ``inbound.get()`` — and is then sent a real interrupt by writing
    ``\\x03`` to the pty master. The tty line discipline turns that into a SIGINT
    for the foreground process group, exactly as pressing Ctrl+C does.

    The interrupt must propagate *into* the debuggee and end the program: with
    the window closed or unreachable, that is a user's only way out. It must not
    be swallowed by the interaction loop's stray-interrupt guard, which covers
    only the narrow window just after a console cell finishes.
    """
    script = tmp_path / "paused_demo.py"
    script.write_text(
        textwrap.dedent(
            """
            import judb
            judb.set_trace(open_browser=False)
            # Sentinel split in two so the traceback's echo of this very line
            # (which the debuggee dies on) cannot itself satisfy the assertion.
            print("RESU" + "MED")
            """
        ).lstrip()
    )

    pid, master = pty.fork()
    if pid == 0:  # child: the pty slave is its stdin/stdout/stderr *and* its ctty
        os.execve(
            sys.executable,
            [sys.executable, str(script)],
            {**os.environ, "JUDB_NO_BROWSER": "1"},
        )
        os._exit(127)  # only reached if exec fails

    seen: list[str] = []
    try:
        url = _read_pty_for_url(master, seen)

        async def wait_until_paused() -> None:
            async with (
                ClientSession() as session,
                session.ws_connect(_ws_url(url)) as ws,
            ):
                await _recv_type(ws, "paused")

        asyncio.run(wait_until_paused())

        os.write(master, b"\x03")  # Ctrl+C on the controlling terminal

        status = _wait_for_exit(pid, timeout=30)
        assert status is not None, f"survived Ctrl+C. output:\n{''.join(seen)}"
        assert os.WIFSIGNALED(status) or os.WEXITSTATUS(status) != 0, (
            f"expected a non-clean exit, got status {status}"
        )
        text = "".join(seen)
        assert "KeyboardInterrupt" in text, f"no KeyboardInterrupt in output:\n{text}"
        # It died at the pause rather than resuming past it.
        assert "RESUMED" not in text
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(master)


def test_bad_frame_index_errors():
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        dbg.set_trace()
        _ = 1

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            await _recv_type(ws, "paused")
            await ws.send_json({"cmd": "select_frame", "index": 999})
            err = await _recv_type(ws, "error")
            assert "frame index" in err["message"]
            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_reassert_sigint_handler_reinstalls_current_handler(
    monkeypatch: pytest.MonkeyPatch,
):
    """On pause, judb pushes Python's SIGINT handler back down to the OS so a
    library that stole it at import (e.g. polars) can't kill terminal Ctrl+C or
    our own `interrupt`. Here: it re-installs whatever handler Python reports."""
    import signal

    current = signal.getsignal(signal.SIGINT)
    calls: list[tuple[int, Any]] = []
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: calls.append((sig, handler))
    )

    Debugger._reassert_sigint_handler()

    assert calls == [(signal.SIGINT, current)]


def test_reassert_sigint_handler_is_noop_off_main_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    """Only the main thread may set signal handlers; for a worker-thread debuggee
    it must be a silent no-op, not a ValueError."""
    import signal

    calls: list[Any] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(signal, "signal", lambda *a: calls.append(a))

    def run() -> None:
        try:
            Debugger._reassert_sigint_handler()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=5)

    assert calls == []
    assert errors == []
