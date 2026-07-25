"""The structured tracebacks behind the Exception pane (``judb/tracebacks.py``).

The pane is fed *structure*, never colour: the browser highlights the source
lines with the same CodeMirror theme as the Source pane, so a theme switch (or a
user's own theme) recolours tracebacks too. These tests pin the shape that
contract depends on — frames with a real source window, the chain order Python
prints, and never raising on a hostile exception.
"""

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

from judb.tracebacks import format_traceback


def chain_from(fn: Callable[[], object]) -> list[dict[str, Any]]:
    """Run ``fn``, which must raise, and return the formatted chain."""
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 — the exception *is* the fixture
        return format_traceback(exc)
    raise AssertionError(f"{fn} should have raised")


def _boom() -> None:
    def inner() -> None:
        raise ValueError("kaboom")

    inner()


def test_chain_describes_the_failing_frame():
    (entry,) = chain_from(_boom)

    assert entry["type"] == "ValueError"
    assert entry["headline"] == ["ValueError: kaboom"]
    assert "relation" not in entry  # nothing chained to it

    frame = entry["frames"][-1]
    assert frame["function"] == "inner"
    assert frame["filename"] == __file__
    # The window is real source read from the file, with the failing line inside
    # it — that is what the frontend highlights.
    assert 'raise ValueError("kaboom")' in "\n".join(frame["lines"])
    assert frame["first_lineno"] <= frame["lineno"]
    assert frame["lineno"] < frame["first_lineno"] + len(frame["lines"])


def test_no_ansi_anywhere():
    """Colour is the frontend's business; nothing here may carry escape codes."""
    assert "\x1b" not in repr(chain_from(_boom))


def test_cause_and_context_chain_oldest_first():
    """`raise ... from ...` comes through in the order Python prints it."""

    def wrap() -> None:
        try:
            1 / 0  # noqa: B018
        except ZeroDivisionError as exc:
            raise ValueError("wrapped") from exc

    chain = chain_from(wrap)
    assert [e["type"] for e in chain] == ["ZeroDivisionError", "ValueError"]
    assert "relation" not in chain[0]
    assert chain[1]["relation"] == "cause"


def test_implicit_context_is_labelled_context():
    def careless() -> None:
        try:
            1 / 0  # noqa: B018
        except ZeroDivisionError:
            raise ValueError("while handling")

    assert chain_from(careless)[1]["relation"] == "context"


def test_suppressed_context_is_dropped():
    def clean() -> None:
        try:
            1 / 0  # noqa: B018
        except ZeroDivisionError:
            raise ValueError("clean") from None

    assert [e["type"] for e in chain_from(clean)] == ["ValueError"]


def test_anchor_columns_point_at_the_failing_expression(tmp_path: Path):
    """Python 3.11+ fine-grained anchors ride along, so the pane can draw the
    `^^^^` markers under the sub-expression that actually failed."""
    script = tmp_path / "anchored.py"
    script.write_text(
        textwrap.dedent("""
        def divide(a, b, c):
            return a / b + a / c
        """).lstrip()
    )
    namespace: dict[str, Any] = {}
    exec(compile(script.read_text(), str(script), "exec"), namespace)  # noqa: S102

    (entry,) = chain_from(lambda: namespace["divide"](1, 1, 0))

    frame = entry["frames"][-1]
    line = frame["lines"][frame["lineno"] - frame["first_lineno"]]
    assert line[frame["col"] : frame["end_col"]] == "a / c"


def test_missing_source_still_describes_the_frame():
    """Code with no readable source (`exec`, a REPL) still gets a located frame.

    A unique pseudo-filename keeps this hermetic: plain ``<string>`` is shared,
    and anything in the process can have planted lines for it in ``linecache``.
    """
    code = compile("raise RuntimeError('no file')", "<judb-test-no-source>", "exec")

    (entry,) = chain_from(lambda: exec(code))  # noqa: S102

    frame = entry["frames"][-1]
    assert frame["filename"] == "<judb-test-no-source>"
    assert frame["function"] == "<module>"
    assert frame["lines"] == []  # nothing to show, but the location still is
    # Without real source we cannot trust the anchor offsets, so they are omitted.
    assert "col" not in frame


def test_never_raises_on_a_hostile_exception():
    """A broken `__str__` must cost us the colour, not the pause."""

    class Hostile(Exception):
        def __str__(self) -> str:
            raise RuntimeError("nope")

    def raise_hostile() -> None:
        raise Hostile

    chain = chain_from(raise_hostile)
    # `type` is the qualified name, the way Python prints it.
    assert chain and chain[0]["type"].endswith("Hostile")
