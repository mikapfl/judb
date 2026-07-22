"""judb — a browser-based visual debugger for scientific Python.

Phase 0 exposes the two composed halves directly: the :class:`Console`
(embedded IPython rich capture) and the :class:`Debugger` (bdb subclass with a
queue-driven interaction loop). The websocket server and browser frontend arrive
in later phases.
"""

from .console import Console
from .debugger import Debugger
from .protocol import CellResult, Output

_active_debugger: Debugger | None = None


def set_trace() -> Debugger:
    """Start (or reuse) a debugger tracing from the caller's frame.

    Phase 0 has no server yet, so this mainly exists so that tests and early
    experiments have a stable entry point; it returns the :class:`Debugger` so
    the caller can drive its command queues.
    """
    import sys

    global _active_debugger
    if _active_debugger is None:
        _active_debugger = Debugger()
    _active_debugger.set_trace(sys._getframe().f_back)
    return _active_debugger


__all__ = ["CellResult", "Console", "Debugger", "Output", "set_trace"]
