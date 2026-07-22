"""A ``bdb.Bdb`` subclass whose interaction loop is driven by a command queue.

This mirrors pudb's model, but instead of urwid keypresses the loop consumes
commands from an inbound queue and emits state/results on an outbound queue.
That queue seam is exactly where Phase 1's websocket server will plug in; here
it also makes the debugger fully drivable from a test.

When the debuggee stops at a line, its thread enters :meth:`interaction`, emits a
``paused`` message, and blocks on ``inbound.get()``. ``execute_cell`` runs a
console cell against the paused frame; ``step``/``next``/``continue``/... set the
appropriate bdb state and return, unblocking the debuggee.
"""

import atexit
import bdb
import linecache
import queue
import time
from collections.abc import Iterable
from types import FrameType, TracebackType
from typing import TYPE_CHECKING, Any

from .console import Console
from .protocol import CellResult

if TYPE_CHECKING:
    from .server import DebugServer


class Debugger(bdb.Bdb):
    def __init__(self, skip: Iterable[str] | None = None) -> None:
        super().__init__(skip=skip)
        self.console = Console()
        self.inbound: queue.Queue[dict[str, Any]] = queue.Queue()
        self.outbound: queue.Queue[dict[str, Any]] = queue.Queue()
        self.current_frame: FrameType | None = None
        self._quitting = False
        self._server: DebugServer | None = None

    # --- bdb hooks --------------------------------------------------------

    def user_line(self, frame: FrameType) -> None:
        """Called by bdb when we stop at a line we care about."""
        self.interaction(frame)

    def user_return(self, frame: FrameType, return_value: object) -> None:
        # Phase 0 does not stop on returns; kept for completeness.
        pass

    def user_exception(
        self,
        frame: FrameType,
        exc_info: tuple[type[BaseException], BaseException, TracebackType],
    ) -> None:
        # Phase 0 does not break on exceptions yet.
        pass

    # --- interaction loop -------------------------------------------------

    def interaction(self, frame: FrameType) -> None:
        self.current_frame = frame
        self._emit_paused(frame)
        while True:
            msg = self.inbound.get()
            cmd = msg.get("cmd")

            if cmd == "execute_cell":
                result = self.console.run_cell(msg.get("code", ""), frame)
                self._emit_cell_result(result)
                continue

            if cmd == "quit":
                self._quitting = True
                self.set_quit()
                return

            if cmd == "step":
                self.set_step()
            elif cmd == "next":
                self.set_next(frame)
            elif cmd == "return":
                self.set_return(frame)
            elif cmd == "continue":
                self.set_continue()
            else:
                self._emit({"type": "error", "message": f"unknown command: {cmd!r}"})
                continue

            # A stepping command was issued: leave the loop so the debuggee runs.
            # The UI disables stepping/console until the next `paused`.
            self._emit({"type": "running"})
            return

    # --- outbound messages ------------------------------------------------

    def _emit(self, message: dict[str, Any]) -> None:
        self.outbound.put(message)

    def _emit_paused(self, frame: FrameType) -> None:
        self._emit(
            {
                "type": "paused",
                "filename": frame.f_code.co_filename,
                "lineno": frame.f_lineno,
                "function": frame.f_code.co_name,
                "locals": sorted(frame.f_locals),
                "source": "".join(linecache.getlines(frame.f_code.co_filename)),
                "stack": self._stack_summary(frame),
            }
        )

    def _emit_cell_result(self, result: CellResult) -> None:
        self._emit(
            {
                "type": "cell_result",
                "success": result.success,
                "outputs": [
                    {"kind": o.kind, "data": o.data, "metadata": o.metadata}
                    for o in result.outputs
                ],
            }
        )

    @staticmethod
    def _stack_summary(frame: FrameType | None) -> list[dict[str, Any]]:
        stack: list[dict[str, Any]] = []
        while frame is not None:
            stack.append(
                {
                    "filename": frame.f_code.co_filename,
                    "lineno": frame.f_lineno,
                    "function": frame.f_code.co_name,
                }
            )
            frame = frame.f_back
        stack.reverse()
        return stack

    # --- server lifecycle -------------------------------------------------

    def start_server(self, *, open_browser: bool = True) -> str:
        """Start the websocket server (once) and return its tokenized URL.

        The server runs on a daemon thread and only touches ``inbound``/
        ``outbound``; cell execution stays on the debuggee thread. Idempotent —
        repeated calls return the existing URL without reopening the browser.
        """
        if self._server is not None:
            return self._server.url

        from .server import DebugServer

        self._server = DebugServer(self)
        url = self._server.start()
        atexit.register(self._notify_finished)
        if open_browser:
            import webbrowser

            webbrowser.open(url)
        return url

    def _notify_finished(self) -> None:
        # Tell the browser the debuggee is done, then give the server thread a
        # brief moment to flush it over the websocket before the process exits.
        self.outbound.put({"type": "finished"})
        time.sleep(0.3)

    # --- convenience entry points ----------------------------------------

    def set_trace(self, frame: FrameType | None = None) -> None:
        """Start tracing from ``frame`` (defaults to the caller's frame)."""
        if frame is None:
            import sys

            frame = sys._getframe().f_back
        super().set_trace(frame)
