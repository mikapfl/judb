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
import sys
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
        # The stopped call stack (outermost-first, matching the `stack` message)
        # and which frame the console + inspection currently target.
        self._frames: list[FrameType] = []
        self._selected = 0
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
        # Freeze the stopped stack; selection starts at the innermost frame.
        self._frames = self._frames_of(frame)
        self._selected = len(self._frames) - 1
        self._emit_paused(frame)
        while True:
            msg = self.inbound.get()
            cmd = msg.get("cmd")

            if cmd == "execute_cell":
                # Run in the *selected* frame, not necessarily the innermost one.
                target = self._frames[self._selected]
                result = self.console.run_cell(msg.get("code", ""), target)
                self._emit_cell_result(result)
                continue

            if cmd == "select_frame":
                self._select_frame(msg.get("index"))
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
                **self._frame_view(frame),
                "stack": self._stack_summary(frame),
                "selected": self._selected,
            }
        )

    def _select_frame(self, index: object) -> None:
        """Retarget console + inspection to stack frame ``index`` (0 = outermost)."""
        if not isinstance(index, int) or not (0 <= index < len(self._frames)):
            self._emit({"type": "error", "message": f"bad frame index: {index!r}"})
            return
        self._selected = index
        self._emit(
            {
                "type": "frame_selected",
                "index": index,
                **self._frame_view(self._frames[index]),
            }
        )

    @staticmethod
    def _frame_view(frame: FrameType) -> dict[str, Any]:
        """The per-frame fields shared by ``paused`` and ``frame_selected``."""
        return {
            "filename": frame.f_code.co_filename,
            "lineno": frame.f_lineno,
            "function": frame.f_code.co_name,
            "locals": sorted(frame.f_locals),
            "source": "".join(linecache.getlines(frame.f_code.co_filename)),
        }

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
    def _frames_of(frame: FrameType | None) -> list[FrameType]:
        """The call stack as frame objects, outermost-first."""
        frames: list[FrameType] = []
        while frame is not None:
            frames.append(frame)
            frame = frame.f_back
        frames.reverse()
        return frames

    def _stack_summary(self, frame: FrameType | None) -> list[dict[str, Any]]:
        # Index-aligned with self._frames / the `selected` index.
        return [
            {
                "filename": f.f_code.co_filename,
                "lineno": f.f_lineno,
                "function": f.f_code.co_name,
            }
            for f in self._frames_of(frame)
        ]

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
        # Always show the URL: the browser may not auto-open (headless / remote /
        # SSH), and the token-in-URL is the only way in.
        print(f"judb: debugger UI at {url}", file=sys.stderr, flush=True)
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
            frame = sys._getframe().f_back
        super().set_trace(frame)
