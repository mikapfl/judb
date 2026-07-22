"""Embedded IPython shell that executes cells in the paused frame's namespace
and captures rich output as Jupyter mime bundles — no ZMQ kernel needed.

The recipe (validated as a spike, see IMPLEMENTATION_PLAN.md §1):

* a custom ``DisplayHook`` captures the value of the last expression instead of
  printing an ``Out[..]`` prompt,
* a custom ``DisplayPublisher`` captures ``display(...)`` calls and flushed
  matplotlib figures,
* ``matplotlib.interactive(True)`` + ``select_figure_formats(shell, {"png"})`` +
  ``matplotlib_inline.flush_figures()`` turn any figure created in a cell into an
  ``image/png`` bundle,
* stdout / stderr are redirected so ``print(...)`` becomes a stream output.

Everything runs on the calling (debuggee) thread, which is what lets a cell touch
the paused frame's real objects.
"""

import contextlib
import io
from types import FrameType
from typing import Any

import matplotlib

# The inline backend renders figures headlessly and feeds them to the display
# publisher via flush_figures(); no GUI / display server required.
matplotlib.use("module://matplotlib_inline.backend_inline")

from IPython.core.displayhook import DisplayHook
from IPython.core.displaypub import DisplayPublisher
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.pylabtools import select_figure_formats
from matplotlib_inline.backend_inline import flush_figures

from .protocol import CellResult, Output

# The buffer the capture classes append to while a cell runs. IPython owns the
# lifecycle of the hook/publisher instances, so a module-level buffer (rather
# than per-instance state) is the tidy seam; cell execution is serialized on the
# debuggee thread, so a single active buffer is sufficient.
_capture: list[Output] | None = None


class _CapturingDisplayHook(DisplayHook):
    """Captures the last expression's value; suppresses the ``Out[..]`` prompt."""

    def write_output_prompt(self) -> None:  # pragma: no cover - trivial
        pass

    def write_format_data(
        self, format_dict: dict[str, Any], md_dict: dict[str, Any] | None = None
    ) -> None:
        if _capture is not None:
            _capture.append(
                Output("execute_result", dict(format_dict), dict(md_dict or {}))
            )

    def finish_displayhook(self) -> None:  # pragma: no cover - trivial
        pass


class _CapturingDisplayPublisher(DisplayPublisher):
    """Captures ``display(...)`` calls and flushed matplotlib figures."""

    def publish(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        source: str | None = None,
        *,
        transient: dict[str, Any] | None = None,
        update: bool = False,
        **kwargs: object,
    ) -> None:
        if _capture is not None:
            _capture.append(Output("display_data", dict(data), dict(metadata or {})))

    def clear_output(self, wait: bool = False) -> None:  # pragma: no cover
        pass


class Console:
    """A reusable embedded IPython console for in-frame cell execution."""

    def __init__(self) -> None:
        # Use the singleton so that display()/get_ipython() route here, which is
        # what makes flush_figures() send figures to our display publisher.
        self.shell: InteractiveShell = InteractiveShell.instance(
            displayhook_class=_CapturingDisplayHook,
            display_pub_class=_CapturingDisplayPublisher,
        )
        matplotlib.interactive(True)
        select_figure_formats(self.shell, {"png"})

    def run_cell(self, code: str, frame: FrameType | None = None) -> CellResult:
        """Execute ``code`` and return the captured rich outputs.

        If ``frame`` is given, the frame's globals and locals are injected into
        the shell namespace first, so the cell sees the paused frame's real
        objects. (For Phase 0, writes land in the shell scratch namespace rather
        than back into the frame — see the risk table in IMPLEMENTATION_PLAN.md.)
        """
        global _capture
        outputs: list[Output] = []
        _capture = outputs  # picked up by the capture classes while the cell runs

        if frame is not None:
            self.shell.user_ns.update(frame.f_globals)
            self.shell.user_ns.update(frame.f_locals)

        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = self.shell.run_cell(code, store_history=True)
                # Turn any figures created by the cell into image/png bundles.
                flush_figures()
        finally:
            _capture = None

        # Assemble outputs in reading order: streams, then rich results/figures.
        final: list[Output] = []
        out_text = stdout.getvalue()
        if out_text:
            final.append(Output.stream("stdout", out_text))
        err_text = stderr.getvalue()
        if err_text:
            final.append(Output.stream("stderr", err_text))
        final.extend(outputs)

        if result.error_before_exec is not None or result.error_in_exec is not None:
            exc = result.error_before_exec or result.error_in_exec
            final.append(
                Output.error(type(exc).__name__, str(exc), traceback=[str(exc)])
            )

        return CellResult(outputs=final, success=result.success)

    def evaluate(self, code: str, frame: FrameType | None = None) -> Any:  # noqa: ANN401
        """Convenience for tests: run ``code`` and return its ``text/plain``."""
        return self.run_cell(code, frame).first_of("text/plain")
