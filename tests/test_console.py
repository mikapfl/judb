"""The embedded IPython console: headless rich capture and frame inspection.

Unit-level coverage of ``judb/console.py`` — what a cell's output turns into
(mime bundles for text, HTML, PNG, streams, errors), the matplotlib-inline
plumbing, and ``inspect`` for the Variables pane.

The console's *composition* with the debugger — pause, run a cell in the paused
frame, get a PNG back — is covered end-to-end over the websocket by
``test_server.py::test_plot_paused_frame_over_websocket``.
"""

import base64
from types import FrameType

import pytest

from judb import Console

# --- console: headless rich capture ---------------------------------------


def test_int_is_plain_text():
    result = Console().run_cell("40 + 2")
    assert result.success
    assert result.first_of("text/plain") == "42"


def test_dataframe_is_html_and_plain():
    result = Console().run_cell("import pandas as pd; pd.DataFrame({'a': [1, 2]})")
    html = result.first_of("text/html")
    assert html is not None and "<table" in html
    assert result.first_of("text/plain") is not None


def test_print_is_a_stream():
    result = Console().run_cell("print('hello from judb')")
    streams = [o for o in result.outputs if o.kind == "stream"]
    assert streams and "hello from judb" in streams[0].data["text"]


def test_introspection_is_captured_not_paged_to_terminal():
    """`obj?` introspection must render inline, not escape to the system pager
    (which would write the docstring to the debuggee's terminal, off in judb)."""
    console = Console()
    console.run_cell("def greet(name):\n    'say hi'\n    return name")
    result = console.run_cell("greet?")
    assert result.success
    info = result.first_of("text/plain")
    assert info is not None and "say hi" in info
    # And nothing leaked out as a stream (that would be the pager fallback).
    assert not [o for o in result.outputs if o.kind == "stream"]


def test_help_is_captured_not_paged_to_terminal():
    """`help(obj)` pages via pydoc (which caches a `less` pager keyed on stdout);
    judb must capture it inline as clean text, not send it to the terminal."""
    console = Console()
    result = console.run_cell("help(len)")
    assert result.success
    info = result.first_of("text/plain")
    assert info is not None
    assert "Return the number of items" in info
    # pydoc's `\b` overstrike bolding must be stripped for the browser.
    assert "\x08" not in info


def test_plot_is_a_png_bundle():
    result = Console().run_cell(
        "import matplotlib.pyplot as plt; plt.plot([1, 2, 3]); None"
    )
    png = result.first_of("image/png")
    assert png is not None
    raw = base64.b64decode(png)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number
    print(f"\n[console] captured PNG bundle: {len(raw)} bytes")


def test_matplotlib_inline_magic_works_and_plots_once():
    """`%matplotlib inline` routes through `enable_gui`, which the base shell
    leaves unimplemented; our inline shell makes it a no-op. A plot afterwards
    must still yield exactly one PNG (the inline post-execute hook and our own
    flush must not double up)."""
    console = Console()
    magic = console.run_cell("%matplotlib inline")
    assert magic.success
    assert not [o for o in magic.outputs if o.kind == "error"]

    result = console.run_cell(
        "import matplotlib.pyplot as plt; plt.plot([1, 2, 3]); None"
    )
    pngs = [
        o
        for o in result.outputs
        if o.kind in ("display_data", "execute_result") and "image/png" in o.data
    ]
    assert len(pngs) == 1


def test_enable_gui_is_noop_for_inline_and_errors_for_real_backends():
    """The inline shell's `enable_gui` (what `%matplotlib inline` calls) is a
    no-op for the inline / no-GUI cases and refuses a real GUI backend with a
    clear message — there's no event loop in the paused console to drive one."""
    shell = Console().shell
    assert shell.enable_gui(None) is None
    assert shell.enable_gui("inline") is None
    with pytest.raises(NotImplementedError, match="inline"):
        shell.enable_gui("qt")


def test_exception_becomes_error_output():
    result = Console().run_cell("1 / 0")
    assert not result.success
    errors = [o for o in result.outputs if o.kind == "error"]
    assert errors and errors[0].data["ename"] == "ZeroDivisionError"


def test_inspect_gets_rich_bundle_for_ipython_display_objects():
    """Values that render via `_ipython_display_` (e.g. plotly figures) must
    still yield their rich mime bundle when inspected in the Variables tree.
    `_ipython_display_` would otherwise hijack `format()`, returning no bundle
    (and self-displaying into nowhere) — so the tree would show only text."""

    class Displayable:
        def _ipython_display_(self) -> None:
            from IPython.display import display

            display({"text/html": "<b>rich</b>"}, raw=True)

        def _repr_html_(self) -> str:
            return "<b>rich</b>"

    def frame_with_local() -> FrameType:
        widget = Displayable()  # noqa: F841 — inspected via the frame
        import sys

        frame = sys._getframe()
        assert frame is not None
        return frame

    result = Console().inspect(frame_with_local(), [["name", "widget"]])
    assert result["repr"].get("text/html") == "<b>rich</b>"
