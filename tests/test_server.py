"""The websocket server: transport, auth, and the whole stack over one socket.

``test_plot_paused_frame_over_websocket`` is the headline flow reduced to a
socket — pause in a frame, plot the paused frame's array *in that frame*, get an
``image/png`` bundle back over the wire, continue to completion — so it doubles
as the smoke test for everything below the browser.
"""

import asyncio
import threading
from urllib.parse import urlparse

import numpy as np
from aiohttp import ClientSession
from helpers import recv_type, ws_url

from judb import Debugger

PLOT_CELL = "import matplotlib.pyplot as plt\nplt.plot(data)"


def test_plot_paused_frame_over_websocket():
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)

    def debuggee() -> None:
        data = np.linspace(0.0, 10.0, 50)
        dbg.set_trace()
        total = 0.0
        for i in range(len(data)):
            total += float(data[i])

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session, session.ws_connect(ws_url(url)) as ws:
            paused = await recv_type(ws, "paused")
            assert "data" in paused["locals"]
            assert "np.linspace" in paused["source"]

            await ws.send_json({"cmd": "execute_cell", "code": PLOT_CELL})
            result = await recv_type(ws, "cell_result")
            pngs = [o for o in result["outputs"] if "image/png" in o["data"]]
            assert pngs, "expected an image/png bundle from plt.plot(data)"

            await ws.send_json({"cmd": "continue"})
            await recv_type(ws, "running")

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
