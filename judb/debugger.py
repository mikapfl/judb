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

import bdb
import queue
from collections.abc import Iterable
from types import FrameType, TracebackType
from typing import Any

from .console import Console
from .protocol import CellResult


class Debugger(bdb.Bdb):
    def __init__(self, skip: Iterable[str] | None = None) -> None:
        super().__init__(skip=skip)
        self.console = Console()
        self.inbound: queue.Queue[dict[str, Any]] = queue.Queue()
        self.outbound: queue.Queue[dict[str, Any]] = queue.Queue()
        self.current_frame: FrameType | None = None
        self._quitting = False

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

            if cmd == "step":
                self.set_step()
            elif cmd == "next":
                self.set_next(frame)
            elif cmd == "return":
                self.set_return(frame)
            elif cmd == "continue":
                self.set_continue()
            elif cmd == "quit":
                self._quitting = True
                self.set_quit()
            else:
                self._emit({"type": "error", "message": f"unknown command: {cmd!r}"})
                continue

            # A stepping command was issued: leave the loop so the debuggee runs.
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

    # --- convenience entry points ----------------------------------------

    def set_trace(self, frame: FrameType | None = None) -> None:
        """Start tracing from ``frame`` (defaults to the caller's frame)."""
        if frame is None:
            import sys

            frame = sys._getframe().f_back
        super().set_trace(frame)
