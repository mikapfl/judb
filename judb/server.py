"""Async web server bridging the debugger's queues to a browser over WebSocket.

Runs on a background **daemon thread** with its own asyncio loop, so the
debuggee thread stays free to block in the debugger's interaction loop (the
threading model in IMPLEMENTATION_PLAN.md §2). The only seam is the debugger's
``inbound``/``outbound`` queues — exactly what Phase 0 already drives from tests
and the terminal demo, so this server is a drop-in replacement for that driver:

    browser ──ws──► inbound.put(cmd)    ...consumed by the paused debuggee thread
    browser ◄─ws─── outbound.get()      ...paused state + rich cell results

An unbounded pump moves ``outbound`` into an asyncio queue so messages emitted
before the browser connects (the first ``paused``) are buffered, not lost. That
buffer only covers messages nobody has read yet; a browser that *re*connects
(refresh, restored tab, a dropped socket the client retried) has missed whatever
the previous connection already consumed, so the most recent state message is
also kept and replayed to it — otherwise the tab comes up blank on a debuggee
that is still paused.

Security: binds to ``127.0.0.1`` on a random port with a random URL token; every
request must carry the token. It executes arbitrary code in the paused frame, so
it must never be reachable off-host.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

if TYPE_CHECKING:
    from .debugger import Debugger

_STATIC = Path(__file__).parent / "static"

# Message types that define the debuggee's current state, as opposed to
# one-shot results (cell output, completions, expansions). The most recent
# one is replayed to a reconnecting browser so a refresh restores the UI.
_STATE_TYPES = frozenset({"paused", "running", "finished"})


class DebugServer:
    """Serves the static frontend and a command WebSocket for one debugger."""

    def __init__(self, debugger: Debugger, host: str = "127.0.0.1") -> None:
        self.dbg = debugger
        self.host = host
        self.token = secrets.token_urlsafe(16)
        self.port: int | None = None
        # The process this server's threads actually run in. `fork()` copies the
        # object but not the threads, so a forked child holding an inherited
        # DebugServer has no server at all — see `Debugger.start_server`.
        self.pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Buffers outbound messages until (and between) websocket connections.
        self._out_q: asyncio.Queue[dict[str, Any]] | None = None
        self._ready = threading.Event()
        # The last message that defines where the debuggee *is*, replayed to a
        # reconnecting browser (see `_handle_ws`). Messages consumed by a
        # previous connection are gone from `_out_q`, so without this a refresh
        # mid-session leaves the tab blank while the debuggee sits paused.
        self._last_state: dict[str, Any] | None = None
        # Whether any browser has connected yet: the first one is brought up to
        # date by the buffered queue, so replaying would only duplicate.
        self._had_client = False

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
        app.router.add_get("/_mpl.js", self._handle_mpl_js)
        # matplotlib's WebAgg toolbar icons (`<img src="_images/…">`), served from
        # matplotlib's own image directory — the same mapping its Tornado backend
        # uses. Static, non-sensitive glyphs, so served without the token (unlike
        # the app/ws). Guarded so a missing dir can never wedge server startup.
        import matplotlib

        images = Path(matplotlib.get_data_path()) / "images"
        if images.is_dir():
            app.router.add_static("/_images/", images)
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
            if msg.get("type") in _STATE_TYPES:
                # Remember it for a reconnecting browser. Rebinding a reference
                # is atomic, so the event-loop thread reads it without a lock.
                self._last_state = msg
            self._loop.call_soon_threadsafe(self._out_q.put_nowait, msg)

    # --- request handlers -------------------------------------------------

    def _check_token(self, request: web.Request) -> None:
        if request.query.get("token") != self.token:
            raise web.HTTPForbidden(text="missing or invalid token")

    async def _handle_index(self, request: web.Request) -> web.FileResponse:
        self._check_token(request)
        index = _STATIC / "index.html"
        if not index.is_file():
            raise web.HTTPInternalServerError(
                text=(
                    "Frontend bundle not built. Run `make frontend` "
                    "(cd frontend && pnpm run build) to generate "
                    f"{index}."
                )
            )
        return web.FileResponse(index)

    async def _handle_mpl_js(self, request: web.Request) -> web.Response:
        """Serve *this* matplotlib's WebAgg client JS, so the interactive-figure
        client always matches the installed backend (see mpl_backend.py). Only
        loaded on demand, when the first interactive figure appears."""
        self._check_token(request)
        from matplotlib.backends.backend_webagg_core import FigureManagerWebAgg

        js = FigureManagerWebAgg.get_javascript()
        return web.Response(text=js, content_type="application/javascript")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        self._check_token(request)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # A *re*connecting browser (refresh, restored tab, a drop the client
        # retried) has missed everything the previous connection consumed, so
        # bring it up to date with where the debuggee stands. The first client
        # needs no replay: the buffered queue still holds those messages.
        # Anything newer that queued while nobody listened follows immediately
        # and supersedes this.
        if self._had_client and self._last_state is not None:
            await ws.send_json(self._last_state)
        self._had_client = True
        sender = asyncio.create_task(self._send_loop(ws))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    # `interrupt` must bypass the queue: the debuggee thread is
                    # blocked inside the runaway cell and won't drain `inbound`
                    # until it finishes. Deliver it straight to that thread.
                    if data.get("cmd") == "interrupt":
                        self.dbg.interrupt()
                    else:
                        self.dbg.inbound.put(data)
        finally:
            sender.cancel()
        return ws

    async def _send_loop(self, ws: web.WebSocketResponse) -> None:
        assert self._out_q is not None
        while not ws.closed:
            await ws.send_json(await self._out_q.get())
