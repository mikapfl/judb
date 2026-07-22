"""Phase 1 exit criterion, verified without a browser.

Drives the *real* websocket server end-to-end: pause in a frame, plot the
paused frame's array in-frame, get an ``image/png`` bundle back over the wire,
then continue to completion. This is the browser's job reduced to a socket.
"""

import asyncio
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
from aiohttp import ClientSession, ClientWebSocketResponse

from judb import Debugger

PLOT_CELL = "import matplotlib.pyplot as plt\nplt.plot(data)"


async def _recv_type(ws: ClientWebSocketResponse, want: str) -> dict[str, Any]:
    """Receive messages until one of type ``want`` arrives."""
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if msg.get("type") == want:
            return msg


def test_plot_paused_frame_over_websocket():
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        data = np.linspace(0.0, 10.0, 50)
        dbg.set_trace()
        total = 0.0
        for i in range(len(data)):
            total += float(data[i])

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url) as ws:
            paused = await _recv_type(ws, "paused")
            assert "data" in paused["locals"]
            assert "np.linspace" in paused["source"]

            await ws.send_json({"cmd": "execute_cell", "code": PLOT_CELL})
            result = await _recv_type(ws, "cell_result")
            pngs = [o for o in result["outputs"] if "image/png" in o["data"]]
            assert pngs, "expected an image/png bundle from plt.plot(data)"

            await ws.send_json({"cmd": "continue"})
            await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_bad_token_is_forbidden():
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)

    async def flow() -> None:
        async with (
            ClientSession() as session,
            session.get(f"http://{q.hostname}:{q.port}/?token=wrong") as r,
        ):
            assert r.status == 403

    asyncio.run(flow())
