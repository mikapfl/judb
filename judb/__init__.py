"""judb — a browser-based visual debugger for scientific Python.

The two composed halves are the :class:`Console` (embedded IPython rich capture)
and the :class:`Debugger` (bdb subclass with a queue-driven interaction loop).
As of Phase 1, :func:`set_trace` also starts a localhost websocket server and
opens a browser tab, so ``breakpoint()`` drops you into the browser UI.
"""

__version__ = "0.1.0"

from .console import Console
from .debugger import Debugger
from .protocol import CellResult, Output

_active_debugger: Debugger | None = None


def set_trace(*, open_browser: bool = True) -> Debugger:
    """Start (or reuse) a debugger tracing from the caller's frame.

    Launches the websocket server and opens a browser tab on first use (pass
    ``open_browser=False`` to skip the tab, e.g. on a headless box). Returns the
    :class:`Debugger` so callers/tests can also drive its command queues directly.
    Wire it up as the ``breakpoint()`` hook via ``PYTHONBREAKPOINT=judb.set_trace``.
    """
    import sys

    global _active_debugger
    if _active_debugger is None:
        _active_debugger = Debugger()
    _active_debugger.start_server(open_browser=open_browser)
    _active_debugger.set_trace(sys._getframe().f_back)
    return _active_debugger


__all__ = ["CellResult", "Console", "Debugger", "Output", "__version__", "set_trace"]
