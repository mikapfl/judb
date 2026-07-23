"""Phase 2 protocol additions, verified over the real websocket without a browser.

`select_frame` retargets the console + inspection to a chosen stack frame: a cell
that fails in the innermost frame (a name defined only in an outer frame) succeeds
after selecting that outer frame.
"""

import asyncio
import queue
import threading
import time
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


def test_interrupt_wakes_a_blocking_call_on_the_main_thread():
    """When the debuggee is the main thread, ``interrupt`` sends a real SIGINT,
    which — like Ctrl+C — breaks out of a blocking C call such as ``time.sleep``,
    not just a pure-Python loop. Driven directly over the queues (no server) so
    the debuggee runs on the test's main thread."""
    dbg = Debugger()
    collected: dict[str, Any] = {}

    def driver() -> None:
        paused = dbg.outbound.get(timeout=15)
        assert paused["type"] == "paused"
        # A cell that would block for 30s in C if not interrupted.
        dbg.inbound.put({"cmd": "execute_cell", "code": "import time; time.sleep(30)"})
        # Wait until the cell is actually running before interrupting it.
        deadline = time.monotonic() + 10
        while not dbg._executing and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)

        started = time.monotonic()
        dbg.interrupt()
        result = dbg.outbound.get(timeout=15)
        collected["result"] = result
        collected["elapsed"] = time.monotonic() - started
        dbg.inbound.put({"cmd": "continue"})

    thread = threading.Thread(target=driver)
    thread.start()

    # Runs the debuggee on *this* (main) thread, so `interrupt` takes the SIGINT
    # route; blocks in the interaction loop until the driver says continue.
    dbg.set_trace()
    _ = 1

    thread.join(timeout=20)
    assert not thread.is_alive()

    result = collected["result"]
    assert result["type"] == "cell_result"
    assert _has_error(result["outputs"], "KeyboardInterrupt")
    # The whole point: we broke the sleep well before its 30s, not waited it out.
    assert collected["elapsed"] < 10


def test_terminal_ctrl_c_while_paused_reaches_the_debuggee():
    """A real terminal Ctrl+C while paused raises KeyboardInterrupt in the
    debuggee thread's idle ``inbound.get()``. It must propagate into the debuggee
    (so the program can be ended without the judb window), *not* be swallowed by
    the stray-interrupt guard — that guard only covers the cell-execution window.
    """

    class InterruptingQueue(queue.Queue[dict[str, Any]]):
        # Stands in for a terminal Ctrl+C landing on the paused thread the instant
        # the interaction loop reaches its idle wait.
        def get(
            self, block: bool = True, timeout: float | None = None
        ) -> dict[str, Any]:
            raise KeyboardInterrupt

    dbg = Debugger()
    dbg.inbound = InterruptingQueue()
    caught: dict[str, Any] = {}

    def debuggee() -> None:
        try:
            dbg.set_trace()
            _ = 1
        except KeyboardInterrupt:
            caught["interrupted"] = True

    thread = threading.Thread(target=debuggee)
    thread.start()
    # The loop emits `paused`, then hits the interrupting get().
    assert dbg.outbound.get(timeout=15)["type"] == "paused"

    thread.join(timeout=10)
    # If the guard had swallowed it, the loop would spin forever and never join.
    assert not thread.is_alive()
    assert caught.get("interrupted") is True


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
