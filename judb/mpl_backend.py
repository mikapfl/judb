"""A matplotlib backend that renders *interactive* figures inside judb.

This reuses matplotlib's transport-agnostic WebAgg core — the same machinery
behind ``%matplotlib notebook`` / ``widget`` — but routes its canvas ⇄ browser
protocol over judb's existing websocket instead of a Jupyter comm or a bundled
Tornado server.

Why this fits judb's threading model: the WebAgg draw loop is entirely
*message-driven* (client event → ``handle_event`` → ``draw_idle`` → ``"draw"`` →
client → ``handle_draw`` → ``refresh_all`` → diff image), so it needs no event
loop of its own. While paused, the debuggee thread already drains ``inbound`` in
the interaction loop; an ``mpl_event`` command is dispatched there, on the very
thread that owns the figure (matplotlib is not thread-safe). Interactivity is
therefore live while paused and simply freezes on ``continue`` — which is fine.

Two channels carry the traffic:

* the **mount notice** — a normal cell output (mime :data:`WEBAGG_MIME`) that
  tells the frontend to create a canvas bound to a figure id, and
* the **live protocol** — out-of-band ``{"type": "mpl", ...}`` messages
  (canvas→browser, via :func:`set_emitter`) and ``mpl_event`` commands
  (browser→canvas, via :func:`dispatch`).
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any, cast

import matplotlib
from matplotlib._pylab_helpers import Gcf
from matplotlib.backend_bases import _Backend
from matplotlib.backends.backend_webagg_core import (
    FigureCanvasWebAggCore,
    FigureManagerWebAgg,
    NavigationToolbar2WebAgg,
)

#: The backend module string (``matplotlib.use(BACKEND)``); ``%matplotlib judb``
#: activates it by the entry-point name ``"judb"`` instead (see pyproject.toml).
BACKEND = "module://judb.mpl_backend"

# Marker set on a figure manager once we've announced it, so switching backends
# (which renumbers figures from 1) can't make a fresh figure collide with an old
# id — a new manager object simply lacks the marker.
_ANNOUNCED = "_judb_announced"

#: Cell-output mime carrying ``{"id": <figure number>}`` — the frontend mounts an
#: interactive canvas for it.
WEBAGG_MIME = "application/vnd.judb.webagg+json"

# Push an out-of-band figure⇄browser message onto judb's outbound channel. The
# debugger wires this to `outbound.put`; None until then (no interactivity yet).
_emit: Callable[[dict[str, Any]], None] | None = None
# Live figure managers by id (str(figure number)). Ids are strings — matplotlib's
# own client uses string figure ids, and figure numbers may themselves be strings
# (`plt.figure("name")`).
_managers: dict[str, FigureManagerWebAgg] = {}


def is_active() -> bool:
    """Whether judb's interactive backend is the current matplotlib backend —
    whether reached via ``%matplotlib judb`` (reports ``"judb"``) or
    ``matplotlib.use(BACKEND)`` (reports the module string)."""
    return matplotlib.get_backend() in (BACKEND, "judb")


def set_emitter(emit: Callable[[dict[str, Any]], None] | None) -> None:
    """Wire the outbound channel used to stream figure⇄browser messages."""
    global _emit
    _emit = emit


def _send(fig_id: str, payload: dict[str, Any]) -> None:
    if _emit is not None:
        _emit({"type": "mpl", "id": fig_id, **payload})


class _JudbSocket:
    """The transport matplotlib's manager writes to (``send_json`` /
    ``send_binary``), bridged to judb's outbound channel, keyed by figure id.

    ``supports_binary`` makes the core use compact diff PNGs; we base64 them into
    JSON (the client's ``_make_on_message`` accepts a ``data:image/png`` string)."""

    supports_binary = True

    def __init__(self, fig_id: str) -> None:
        self.fig_id = fig_id

    def send_json(self, content: dict[str, Any]) -> None:
        _send(self.fig_id, {"json": content})

    def send_binary(self, blob: bytes) -> None:
        _send(self.fig_id, {"blob": base64.b64encode(blob).decode("ascii")})


class FigureManagerJudb(FigureManagerWebAgg):
    # Stock FigureManagerWebAgg sets `_toolbar2_class = None` (so it doesn't break
    # ipympl); we want the WebAgg navigation toolbar so the pan/zoom/home buttons
    # actually work. show() is inherited as a no-op — judb announces figures
    # itself (see announce_new_figures).
    _toolbar2_class = NavigationToolbar2WebAgg


class FigureCanvasJudb(FigureCanvasWebAggCore):
    # The manager is created from `canvas.manager_class` (not the backend's
    # `FigureManager`), so point it at our toolbar-enabled manager. Overriding the
    # base's `classproperty` with a plain attribute is enough.
    manager_class = FigureManagerJudb


@_Backend.export
class _BackendJudb(_Backend):
    FigureCanvas = FigureCanvasJudb
    FigureManager = FigureManagerJudb


def announce_new_figures() -> list[str]:
    """Register a socket for every not-yet-announced open figure and return their
    ids, so the console can emit a mount notice for each. Called at cell end (the
    judb analogue of matplotlib-inline's ``flush_figures``)."""
    new: list[str] = []
    for raw in Gcf.get_all_fig_managers():
        # Our backend only ever makes FigureManagerJudb (a FigureManagerWebAgg);
        # Gcf is typed to the base, so narrow it for the WebAgg-only API.
        manager = cast(FigureManagerWebAgg, raw)
        if getattr(manager, _ANNOUNCED, False):
            continue
        fig_id = str(manager.num)
        manager.add_web_socket(_JudbSocket(fig_id))
        setattr(manager, _ANNOUNCED, True)
        _managers[fig_id] = manager
        new.append(fig_id)
    return new


def dispatch(fig_id: str, content: dict[str, Any]) -> None:
    """Feed one browser event to a figure's canvas (runs on the debuggee thread
    via the interaction loop)."""
    manager = _managers.get(fig_id)
    if manager is None:
        return
    # `supports_binary` / `closing` are socket-level in matplotlib's transports
    # (see nbagg's CommSocket.on_message), not canvas events — don't forward them
    # to `handle_json` (which would log "unhandled message type"). We always
    # support binary; on close, drop the figure.
    kind = content.get("type")
    if kind == "supports_binary":
        return
    if kind == "closing":
        for socket in list(manager.web_sockets):
            manager.remove_web_socket(socket)
        _managers.pop(fig_id, None)
        return
    manager.handle_json(content)


def reset() -> None:
    """Forget all tracked figures (used by tests for isolation)."""
    _managers.clear()
