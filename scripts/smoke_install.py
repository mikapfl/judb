"""Post-install smoke check — run *inside a fresh venv* that has ``pip install``ed
a built judb wheel/sdist, with the repo NOT on ``sys.path`` (run it from another
cwd, e.g. ``/``).

Proves the packaging & install story (PHASE3_PLAN.md A1): the frontend bundle
ships inside the artifact, the server serves it, and a headless ``set_trace``
round-trip works using **only judb's runtime dependencies**. In particular it
imports no numpy/pandas — those are dev-only now, so a fresh ``pip install judb``
must not need them. Mirrors ``tests/test_server.py``, reduced to what a clean
install has to satisfy.

Driven by ``scripts/smoke_install.sh`` / ``make smoke``.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientSession, ClientWebSocketResponse

import judb
from judb import Debugger

PLOT_CELL = "import matplotlib.pyplot as plt\nplt.plot(data)"


def _fail(msg: str) -> None:
    raise SystemExit(f"smoke FAIL: {msg}")


def _assert_bundle_shipped() -> Path:
    """The built frontend bundle must live inside the *installed* package."""
    static = Path(judb.__file__).resolve().parent / "static" / "index.html"
    if not static.is_file() or static.stat().st_size < 1000:
        _fail(f"frontend bundle missing/empty in install: {static}")
    # Guard against importing the source checkout instead of the installed
    # package (which would make this whole check meaningless).
    if "site-packages" not in str(static):
        _fail(f"judb imported from {static}, not from an install")
    return static


async def _recv_type(
    ws: ClientWebSocketResponse, want: str, timeout: float = 10
) -> dict[str, Any]:
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
        if msg.get("type") == want:
            return msg


def main() -> None:
    static = _assert_bundle_shipped()

    dbg = Debugger()
    url = dbg.start_server(open_browser=False)
    q = urlparse(url)
    token = parse_qs(q.query)["token"][0]
    ws_url = f"ws://{q.hostname}:{q.port}/ws?token={token}"

    def debuggee() -> None:
        data = [0.0, 1.0, 2.0, 3.0, 4.0]  # plain list: numpy is dev-only now
        dbg.set_trace()
        total = 0.0
        for x in data:
            total += x

    thread = threading.Thread(target=debuggee)
    thread.start()

    async def flow() -> None:
        async with ClientSession() as session:
            # The server serves the bundle over HTTP (with the token).
            async with session.get(url) as r:
                body = await r.text()
                if r.status != 200 or "<" not in body:
                    _fail(f"server did not serve the bundle (status {r.status})")
            # …and the full pause → plot-in-frame → continue round-trip works.
            async with session.ws_connect(ws_url) as ws:
                paused = await _recv_type(ws, "paused")
                if "data" not in paused["locals"]:
                    _fail("paused frame is missing the expected local `data`")
                await ws.send_json({"cmd": "execute_cell", "code": PLOT_CELL})
                result = await _recv_type(ws, "cell_result")
                if not [o for o in result["outputs"] if "image/png" in o["data"]]:
                    _fail("no image/png bundle from plt.plot(data) in a fresh install")
                await ws.send_json({"cmd": "continue"})
                await _recv_type(ws, "running")

    asyncio.run(flow())
    thread.join(timeout=5)
    if thread.is_alive():
        _fail("debuggee did not finish after `continue`")
    print(
        f"smoke OK — bundle {static.stat().st_size} B, served + round-tripped at {url}"
    )


if __name__ == "__main__":
    main()
