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
import ctypes
import linecache
import os
import queue
import signal
import sys
import threading
import time
from collections.abc import Iterable
from types import FrameType, TracebackType
from typing import TYPE_CHECKING, Any

from . import mpl_backend
from .console import Console
from .protocol import CellResult

if TYPE_CHECKING:
    from .server import DebugServer


class Debugger(bdb.Bdb):
    def __init__(
        self, skip: Iterable[str] | None = None, **_pdb_kwargs: object
    ) -> None:
        # `pytest --pdbcls=judb:Debugger` wraps us in a `pdb.Pdb`-style subclass
        # and may instantiate with pdb kwargs (completekey/stdin/stdout/nosigint/
        # readrc). We drive the UI over a websocket, not a terminal Cmd loop, so
        # we accept and ignore them. See PHASE3_PLAN.md A2.
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
        # Post-mortem state: set when we enter `interaction` with a traceback
        # (e.g. `pytest --pdb`) rather than a live paused frame. In that mode the
        # program has already unwound, so stepping just leaves the debugger, and
        # `_exc_info` (type, message) rides along on the `paused` message.
        self._is_postmortem = False
        self._exc_info: tuple[str, str] | None = None
        # Set while paused at the outermost traced frame's return (see
        # `user_return`): the debuggee has finished, so resuming must stop
        # tracing rather than step onward into interpreter shutdown.
        self._at_exit_return = False
        # Identity of the debuggee thread (the one that blocks in the interaction
        # loop and runs cells), and whether a cell is executing right now — both
        # read by `interrupt`, which fires a KeyboardInterrupt into that thread
        # from the *server* thread to stop a runaway cell.
        self._debuggee_tid: int | None = None
        self._executing = False
        # Route interactive-matplotlib (WebAgg) figure⇄browser messages onto our
        # outbound channel. Events flow back as `mpl_event` commands, handled on
        # this (debuggee) thread — the one that owns the figures. See mpl_backend.
        mpl_backend.set_emitter(self._emit)

    # --- bdb hooks --------------------------------------------------------

    def user_line(self, frame: FrameType) -> None:
        """Called by bdb when we stop at a line we care about."""
        self.interaction(frame)

    def user_return(self, frame: FrameType, return_value: object) -> None:
        """Stop when the *outermost traced frame* returns — the debuggee is done.

        Without this, tracing outlives the debuggee's own code: with
        ``judb.set_trace()`` on the last line of a script there is no further
        line event in user code, so the next one lands in interpreter shutdown
        internals (``threading._shutdown``) and the browser shows stdlib frames
        instead of the user's program. Stopping at ``botframe``'s return gives a
        final look at their own frame instead (pdb shows ``--Return--`` here).

        Every *other* return stays silent, so ``step``/``next``/``return`` keep
        their current semantics.
        """
        if frame is not self.botframe:
            return
        self._at_exit_return = True
        try:
            self.interaction(frame)
        finally:
            self._at_exit_return = False

    def user_exception(
        self,
        frame: FrameType,
        exc_info: tuple[type[BaseException], BaseException, TracebackType],
    ) -> None:
        # Phase 0 does not break on exceptions yet.
        pass

    # --- interaction loop -------------------------------------------------

    def interaction(
        self,
        frame: FrameType | None,
        tb_or_exc: TracebackType | BaseException | None = None,
    ) -> None:
        """Pause and drive the UI until a resume command returns.

        Two entry shapes:

        * **Live pause** — ``interaction(frame)`` from ``user_line`` / our
          ``set_trace``. The stack is the frame's ``f_back`` chain.
        * **Post-mortem** — ``interaction(None, exc_or_tb)``, as ``pytest --pdb``
          calls it after a failure (on 3.13 it passes the *exception*, older
          Pythons a traceback). The stack comes from the traceback chain, and
          the program has already unwound, so stepping only leaves the debugger.
        """
        exc: BaseException | None = None
        tb: TracebackType | None = None
        if isinstance(tb_or_exc, BaseException):
            exc, tb = tb_or_exc, tb_or_exc.__traceback__
        elif tb_or_exc is not None:
            tb = tb_or_exc

        self._is_postmortem = frame is None and tb is not None
        if self._is_postmortem:
            assert tb is not None  # narrowed by the guard above
            self._frames = self._frames_of_traceback(tb)
            # `pytest --pdb` reaches us via post-mortem without going through an
            # entry point that starts the UI server (judb.set_trace / -m judb
            # do). Bring it up here. Safe only on this branch: the live path is
            # driven by callers that read `outbound` directly (the unit tests),
            # and a server's outbound pump would race them — post-mortem has no
            # such competing reader (the browser is the only consumer).
            self.start_server()
        else:
            self._frames = self._frames_of(frame)
        # Selection starts at the innermost frame (the failing one, post-mortem).
        self._selected = len(self._frames) - 1
        target = self._frames[self._selected]
        self.current_frame = target
        self._debuggee_tid = threading.get_ident()
        self._exc_info = (type(exc).__name__, str(exc)) if exc is not None else None

        self._reassert_sigint_handler()
        self._emit_paused(target)
        while True:
            # The idle wait is deliberately *outside* the guard below: a real
            # terminal Ctrl+C while paused (this thread is the main thread, so
            # SIGINT raises KeyboardInterrupt right here) must propagate into the
            # debuggee — the only way to end the program without the judb window —
            # rather than being swallowed.
            msg = self.inbound.get()
            try:
                if self._handle(target, msg):
                    return
            except KeyboardInterrupt:
                # A late `interrupt` aimed at a just-finished cell that landed in
                # the narrow post-cell window; swallow it so it can't derail the
                # loop. (An interrupt *during* the cell is already caught by
                # IPython inside run_cell.)
                continue

    def _handle(self, frame: FrameType, msg: dict[str, Any]) -> bool:
        """Process one inbound command. Returns True to leave the loop (resume)."""
        cmd = msg.get("cmd")

        if cmd == "execute_cell":
            # Run in the *selected* frame, not necessarily the innermost one.
            target = self._frames[self._selected]
            # Mark the window in which `interrupt` may fire (see `interrupt`).
            self._executing = True
            try:
                result = self.console.run_cell(msg.get("code", ""), target)
            finally:
                self._executing = False
            self._emit_cell_result(result)
            return False

        if cmd == "select_frame":
            self._select_frame(msg.get("index"))
            return False

        if cmd == "complete":
            self._complete(msg.get("code", ""), msg.get("cursor", 0))
            return False

        if cmd == "mpl_event":
            # An interactive-figure event (zoom/pan/resize/draw). Dispatched here,
            # on the debuggee thread that owns the figure, while paused.
            fig_id, content = msg.get("id"), msg.get("content")
            if isinstance(fig_id, str) and isinstance(content, dict):
                mpl_backend.dispatch(fig_id, content)
            return False

        if cmd == "mpl_download":
            # Render a figure via savefig (png/svg/pdf/…) on this thread — the
            # WebAgg canvas is raster, so vector formats can only come from here.
            fig_id, fmt = msg.get("id"), msg.get("format")
            if isinstance(fig_id, str) and isinstance(fmt, str):
                mpl_backend.download(fig_id, fmt)
            return False

        if cmd == "expand":
            self._expand(msg.get("path"))
            return False

        if cmd in ("set_break", "clear_break"):
            self._toggle_break(cmd, msg.get("filename"), msg.get("line"))
            return False

        if cmd == "quit":
            self._quitting = True
            self.set_quit()
            return True

        if self._is_postmortem:
            # The program already unwound; there is nothing to step through. Any
            # resume command simply leaves the debugger (the caller — e.g.
            # pytest's post_mortem — then proceeds past the failed test).
            if cmd in ("step", "next", "return", "continue"):
                self._emit({"type": "running"})
                return True
            self._emit({"type": "error", "message": f"unknown command: {cmd!r}"})
            return False

        if self._at_exit_return and cmd in ("step", "next", "return", "continue"):
            # Paused at the outermost frame's return: the debuggee's code is
            # finished, so *any* resume ends tracing. Stepping onward from here
            # would only walk into interpreter shutdown internals.
            self.set_continue()
            self._emit({"type": "running"})
            return True

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
            return False

        # A stepping command was issued: leave the loop so the debuggee runs.
        # The UI disables stepping/console until the next `paused`.
        self._emit({"type": "running"})
        return True

    def interrupt(self) -> None:
        """Raise ``KeyboardInterrupt`` in the debuggee thread to stop a runaway
        console cell.

        Called from the *server* thread: the debuggee thread is busy inside the
        cell and not reading ``inbound``, so a queued command would never be seen
        until the cell finished — which is the whole problem. It fires only while
        a cell is executing (``_executing`` guards the loop and other commands).

        Two routes, because they differ in what they can wake:

        * **Main-thread debuggee → a real SIGINT** (``pthread_kill``). Python runs
          its SIGINT handler on the main thread, so this raises ``KeyboardInterrupt``
          the way Ctrl+C does — out of a blocking C call (``time.sleep``, I/O) via
          the syscall's ``EINTR`` *and* out of a pure-Python loop via the eval
          breaker. This is the reliable path and covers the usual
          ``judb.set_trace()``-in-a-script case.
        * **Otherwise → the async-exception API** (``SetAsyncExc``). A worker
          thread can't run Python's signal handler, so we inject the exception
          directly. It lands at the target's next bytecode, so it stops a
          pure-Python runaway but a call blocked in a C extension unwinds only
          once it returns to Python. (Also the fallback where ``pthread_kill`` is
          unavailable, e.g. Windows.)
        """
        tid = self._debuggee_tid
        if tid is None or not self._executing:
            return
        if tid == threading.main_thread().ident and hasattr(signal, "pthread_kill"):
            signal.pthread_kill(tid, signal.SIGINT)
        else:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(tid), ctypes.py_object(KeyboardInterrupt)
            )

    @staticmethod
    def _reassert_sigint_handler() -> None:
        """Re-install Python's SIGINT handler at the OS level whenever we pause.

        Some C/Rust extensions install their *own* SIGINT handler when imported
        (``polars`` is one), stealing it from Python at the OS level. ``signal.
        getsignal`` still reports Python's handler — Python doesn't know it was
        overwritten — but the OS no longer routes Ctrl+C through Python, so two
        things silently break while paused: a real terminal Ctrl+C no longer
        wakes the ``inbound.get()`` blocking this thread (the only way to end the
        program without the judb window), and our own ``interrupt`` (which does
        ``pthread_kill(SIGINT)`` for a main-thread debuggee) is swallowed too.

        Re-asserting the handler Python believes is current pushes Python's
        trampoline back down to the OS without clobbering a user's custom handler.
        Only the main thread may set signal handlers, and SIGINT-based interrupts
        only apply there, so this is a no-op for a worker-thread debuggee.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        handler = signal.getsignal(signal.SIGINT)
        if handler is not None:
            signal.signal(signal.SIGINT, handler)

    # --- outbound messages ------------------------------------------------

    def _emit(self, message: dict[str, Any]) -> None:
        self.outbound.put(message)

    def _emit_paused(self, frame: FrameType) -> None:
        message: dict[str, Any] = {
            "type": "paused",
            **self._frame_view(frame),
            "stack": self._stack_summary(),
            "selected": self._selected,
        }
        if self._is_postmortem:
            message["postmortem"] = True
        if self._at_exit_return:
            message["exiting"] = True
        if self._exc_info is not None:
            message["exception"] = {
                "type": self._exc_info[0],
                "message": self._exc_info[1],
            }
        self._emit(message)

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

    def _complete(self, code: object, cursor: object) -> None:
        """Tab-completion for a console cell, run against the *selected* frame."""
        if not isinstance(code, str) or not isinstance(cursor, int):
            self._emit({"type": "error", "message": "bad complete request"})
            return
        target = self._frames[self._selected]
        replace_from, matches = self.console.complete(code, cursor, target)
        self._emit(
            {
                "type": "completions",
                "from": replace_from,
                "matches": matches,
            }
        )

    def _expand(self, path: object) -> None:
        """Lazily resolve a variable ``path`` in the selected frame (see Console)."""
        if not isinstance(path, list):
            self._emit({"type": "error", "message": f"bad expand path: {path!r}"})
            return
        target = self._frames[self._selected]
        try:
            node = self.console.inspect(target, path)
        except Exception as exc:  # noqa: BLE001 — surface any resolution failure to the UI
            self._emit(
                {
                    "type": "expanded",
                    "path": path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return
        self._emit({"type": "expanded", "path": path, **node})

    def _toggle_break(self, cmd: str, filename: object, line: object) -> None:
        """Set/clear a line breakpoint from the source gutter, then echo the
        file's breakpoint lines back so the gutter can redraw.

        bdb keeps tracing installed after ``continue`` whenever any breakpoint
        exists, so a breakpoint set while paused fires on the next ``continue``.
        Runs on the debuggee thread (like ``expand``), so touching ``self.breaks``
        is race-free.
        """
        if not isinstance(filename, str) or not isinstance(line, int):
            self._emit({"type": "error", "message": "bad breakpoint request"})
            return
        # set_break returns an error string (e.g. "line has no code"); None on ok.
        err = (
            self.set_break(filename, line)
            if cmd == "set_break"
            else self.clear_break(filename, line)
        )
        message: dict[str, Any] = {
            "type": "breakpoints",
            "filename": filename,
            "lines": sorted(self.get_file_breaks(filename)),
        }
        if err:
            message["error"] = err
        self._emit(message)

    def _frame_view(self, frame: FrameType) -> dict[str, Any]:
        """The per-frame fields shared by ``paused`` and ``frame_selected``."""
        return {
            "filename": frame.f_code.co_filename,
            "lineno": frame.f_lineno,
            "function": frame.f_code.co_name,
            "locals": sorted(frame.f_locals),
            "source": "".join(linecache.getlines(frame.f_code.co_filename)),
            "breakpoints": sorted(self.get_file_breaks(frame.f_code.co_filename)),
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

    @staticmethod
    def _frames_of_traceback(tb: TracebackType) -> list[FrameType]:
        """The frames of a traceback chain, outermost-first.

        A traceback runs outermost→innermost (``tb_next`` goes deeper toward the
        ``raise``), so this matches ``_frames_of``'s ordering and leaves the
        failing frame last — where post-mortem selection starts.
        """
        frames: list[FrameType] = []
        cur: TracebackType | None = tb
        while cur is not None:
            frames.append(cur.tb_frame)
            cur = cur.tb_next
        return frames

    def _stack_summary(self) -> list[dict[str, Any]]:
        # Index-aligned with self._frames / the `selected` index. Reads the
        # frozen stack directly so it is correct for post-mortem (traceback)
        # frames too, whose f_back chain would not reproduce the failure stack.
        return [
            {
                "filename": f.f_code.co_filename,
                "lineno": f.f_lineno,
                "function": f.f_code.co_name,
            }
            for f in self._frames
        ]

    # --- server lifecycle -------------------------------------------------

    def start_server(self, *, open_browser: bool = True) -> str:
        """Start the websocket server (once) and return its tokenized URL.

        The server runs on a daemon thread and only touches ``inbound``/
        ``outbound``; cell execution stays on the debuggee thread. Idempotent —
        repeated calls return the existing URL without reopening the browser.

        Setting ``JUDB_NO_BROWSER`` in the environment suppresses the browser
        tab regardless of ``open_browser`` (handy for headless boxes, CI, and
        driving pytest's ``--pdb`` entry point from a test).
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
        if open_browser and os.environ.get("JUDB_NO_BROWSER") is None:
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
