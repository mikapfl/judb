"""Phase 0 acceptance tests.

Exit criterion (IMPLEMENTATION_PLAN.md §5): stop at a breakpoint, run a cell
in-frame, see a PNG bundle. These tests exercise the two composed halves — the
embedded rich-capture console and the queue-driven bdb debugger.
"""

import base64
import threading
from typing import Any

import numpy as np

from judb import Console, Debugger

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


def test_exception_becomes_error_output():
    result = Console().run_cell("1 / 0")
    assert not result.success
    errors = [o for o in result.outputs if o.kind == "error"]
    assert errors and errors[0].data["ename"] == "ZeroDivisionError"


# --- debugger + console composition ---------------------------------------


def _target(dbg: Debugger) -> float:
    """A tiny 'scientific' function with an in-scope array to plot."""
    data = np.linspace(0.0, 10.0, 50)
    dbg.set_trace()  # execution stops at the *next* line
    total = float(data.sum())
    return total


def test_stop_run_cell_in_frame_get_png():
    """The headline Phase 0 flow: pause, plot the paused frame's array, continue."""
    dbg = Debugger()
    collected: dict[str, Any] = {}

    def driver() -> None:
        paused = dbg.outbound.get(timeout=15)
        assert paused["type"] == "paused"
        assert paused["function"] == "_target"
        assert "data" in paused["locals"]  # array in the paused frame
        collected["paused"] = paused

        # Plot the array that lives *only* in the paused frame's namespace.
        dbg.inbound.put(
            {
                "cmd": "execute_cell",
                "code": "import matplotlib.pyplot as plt; plt.plot(data); None",
            }
        )
        result = dbg.outbound.get(timeout=15)
        assert result["type"] == "cell_result"
        collected["result"] = result

        dbg.inbound.put({"cmd": "continue"})

    thread = threading.Thread(target=driver)
    thread.start()
    total = _target(dbg)  # blocks in the interaction loop while paused
    thread.join(timeout=20)
    assert not thread.is_alive()

    # The debuggee ran to completion after 'continue'.
    assert total == 250.0

    # A PNG was rendered from the paused frame's real array.
    result = collected["result"]
    png_outputs = [o for o in result["outputs"] if "image/png" in o["data"]]
    assert png_outputs, f"no PNG in outputs: {result['outputs']}"
    raw = base64.b64decode(png_outputs[0]["data"]["image/png"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    print(
        f"\n[debugger] paused in {collected['paused']['function']}, "
        f"plotted in-frame array -> PNG {len(raw)} bytes"
    )
