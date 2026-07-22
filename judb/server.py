"""Async web server bridging the debugger's queues to a browser over WebSocket.

Runs on a background **daemon thread** with its own asyncio loop, so the
debuggee thread stays free to block in the debugger's interaction loop (the
threading model in IMPLEMENTATION_PLAN.md §2). The only seam is the debugger's
``inbound``/``outbound`` queues — exactly what Phase 0 already drives from tests
and the terminal demo, so this server is a drop-in replacement for that driver:

    browser ──ws──► inbound.put(cmd)    ...consumed by the paused debuggee thread
    browser ◄─ws─── outbound.get()      ...paused state + rich cell results

An unbounded pump moves ``outbound`` into an asyncio queue so messages emitted
before the browser connects (the first ``paused``) are buffered, not lost.

Security: binds to ``127.0.0.1`` on a random port with a random URL token; every
request must carry the token. It executes arbitrary code in the paused frame, so
it must never be reachable off-host.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

if TYPE_CHECKING:
    from .debugger import Debugger

_STATIC = Path(__file__).parent / "static"


class DebugServer:
    """Serves the static frontend and a command WebSocket for one debugger."""

    def __init__(self, debugger: Debugger, host: str = "127.0.0.1") -> None:
        self.dbg = debugger
        self.host = host
        self.token = secrets.token_urlsafe(16)
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Buffers outbound messages until (and between) websocket connections.
        self._out_q: asyncio.Queue[dict[str, Any]] | None = None
        self._ready = threading.Event()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?token={self.token}"

    def start(self) -> str:
        """Start the server thread and block until it is listening."""
        threading.Thread(target=self._run, name="judb-server", daemon=True).start()
        self._ready.wait()
        return self.url

    # --- server thread ----------------------------------------------------

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._out_q = asyncio.Queue()
        self._loop.run_until_complete(self._serve())
        # A *dedicated daemon* thread does the blocking outbound.get(): the
        # default executor's non-daemon workers would be joined at interpreter
        # shutdown, and a get() that never returns would then deadlock exit.
        threading.Thread(
            target=self._pump_outbound, name="judb-pump", daemon=True
        ).start()
        self._ready.set()
        self._loop.run_forever()

    async def _serve(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, 0)
        await site.start()
        # Port 0 → the OS picked one; read it back from the bound socket.
        self.port = runner.addresses[0][1]

    def _pump_outbound(self) -> None:
        """Move the debugger's (blocking) outbound queue into the asyncio queue."""
        assert self._loop is not None and self._out_q is not None
        while True:
            msg = self.dbg.outbound.get()
            self._loop.call_soon_threadsafe(self._out_q.put_nowait, msg)

    # --- request handlers -------------------------------------------------

    def _check_token(self, request: web.Request) -> None:
        if request.query.get("token") != self.token:
            raise web.HTTPForbidden(text="missing or invalid token")

    async def _handle_index(self, request: web.Request) -> web.FileResponse:
        self._check_token(request)
        return web.FileResponse(_STATIC / "index.html")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        self._check_token(request)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        sender = asyncio.create_task(self._send_loop(ws))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self.dbg.inbound.put(json.loads(msg.data))
        finally:
            sender.cancel()
        return ws

    async def _send_loop(self, ws: web.WebSocketResponse) -> None:
        assert self._out_q is not None
        while not ws.closed:
            await ws.send_json(await self._out_q.get())
