"""Phase 2 protocol additions, verified over the real websocket without a browser.

`select_frame` retargets the console + inspection to a chosen stack frame: a cell
that fails in the innermost frame (a name defined only in an outer frame) succeeds
after selecting that outer frame.
"""

import asyncio
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

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
