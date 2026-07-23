"""The websocket server: transport, auth, and the whole stack over one socket.

``test_plot_paused_frame_over_websocket`` is the headline flow reduced to a
socket — pause in a frame, plot the paused frame's array *in that frame*, get an
``image/png`` bundle back over the wire, continue to completion — so it doubles
as the smoke test for everything below the browser.
"""

import asyncio
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pytest
from aiohttp import ClientSession
from helpers import URL_LINE, recv_type, ws_url

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


def test_reconnect_replays_current_state():
    """A refreshed browser tab gets the paused state back.

    Whatever the previous connection consumed is gone from the outbound buffer,
    so without a replay the reconnecting tab sits blank while the debuggee is
    still paused and waiting — the common case of hitting F5.
    """
    dbg = Debugger()
    url = dbg.start_server(open_browser=False)

    def debuggee() -> None:
        recovered = "STATE"  # noqa: F841 — read back from the replayed frame
        dbg.set_trace()
        _ = 1

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session:
            async with session.ws_connect(ws_url(url)) as ws:
                first = await recv_type(ws, "paused")
                assert "recovered" in first["locals"]
                # The first client is served by the buffer, so it must not also
                # receive a replayed duplicate.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(ws.receive_json(), timeout=1)

            # The tab is closed and reopened: same state, still driveable.
            async with session.ws_connect(ws_url(url)) as ws:
                again = await recv_type(ws, "paused")
                assert again["function"] == first["function"]
                assert again["lineno"] == first["lineno"]
                assert again["locals"] == first["locals"]
                assert again["source"] == first["source"]

                await ws.send_json({"cmd": "continue"})
                await recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_forked_child_gets_its_own_server(tmp_path: Path):
    """A forked child must not inherit the parent's thread-less server.

    ``fork()`` copies the ``DebugServer`` object but not the threads running it,
    so a child that reused it would pause with no event loop, no outbound pump
    and no printed URL — a silent hang with no way in.
    """
    script = tmp_path / "fork_demo.py"
    script.write_text(
        textwrap.dedent(
            """
            import multiprocessing as mp
            import judb
            from judb import Debugger

            # The parent owns a running server, as it would after its own pause.
            judb._active_debugger = Debugger()
            judb._active_debugger.start_server(open_browser=False)

            def child():
                judb.set_trace(open_browser=False)   # inherits _active_debugger

            if __name__ == "__main__":
                mp.set_start_method("fork", force=True)
                proc = mp.Process(target=child)
                proc.start()
                proc.join(timeout=5)
                print("CHILD_PAUSED", proc.is_alive(), flush=True)
                proc.terminate()
            """
        ).lstrip()
    )
    out = subprocess.run(
        [sys.executable, str(script)],
        check=False,  # the script terminates its paused child, so rc is not 0
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "JUDB_NO_BROWSER": "1"},
    )
    combined = out.stdout + out.stderr
    urls = set(URL_LINE.findall(combined))
    # Two servers on two ports: the parent's and the child's own.
    assert len(urls) == 2, f"expected parent + child servers, got {urls}\n{combined}"
    # And the child really is paused, waiting on its own UI.
    assert "CHILD_PAUSED True" in combined, combined
