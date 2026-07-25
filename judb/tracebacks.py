"""Structured tracebacks for the Exception pane.

judb deliberately ships the *structure* of a traceback — file, line, function,
and the surrounding source lines — rather than a pre-coloured (ANSI) rendering
of it. The frontend then highlights those source lines with the very same
CodeMirror highlighter the Source pane and the console cells use, so colour is a
pure theme concern: switching (or overriding) the theme recolours tracebacks
with no backend involvement, and the debugger never has to guess which Pygments
style the browser is painting with.

Everything here is stdlib :mod:`traceback` — no IPython — so it is cheap, has no
colour opinion of its own, and works on any exception.
"""

import linecache
import traceback
from collections.abc import Callable
from typing import Any

#: Source lines shown before / after the failing line of each traceback frame.
#: Asymmetric on purpose: leading context is what makes a failing statement
#: readable, while trailing lines mostly cost height in a pane that stacks one
#: window per frame (and the Source pane is right there for the rest).
CONTEXT_BEFORE = 2
CONTEXT_AFTER = 1


def format_traceback(
    exc: BaseException, canonic: Callable[[str], str] | None = None
) -> list[dict[str, Any]]:
    """Describe ``exc`` and its chained causes as plain, JSON-ready dicts.

    The result is the exception *chain* in the order Python prints it: the
    oldest cause first, the exception that actually surfaced last. Each entry
    carries ``type``, the ``headline`` lines (what ``format_exception_only``
    would print — including a ``SyntaxError``'s caret and any ``__notes__``),
    its ``frames``, and — from the second entry on — a ``relation`` saying how
    it follows the previous one (``"cause"`` for ``raise ... from ...``,
    ``"context"`` for an error raised while handling another).

    Each frame is ``filename``/``lineno``/``function`` plus ``lines`` (a small
    source window) and ``first_lineno`` (the 1-based number of ``lines[0]``),
    and optionally ``col``/``end_col``: Python 3.11+ fine-grained anchor offsets
    into the failing line, for the ``~~~^~~~`` markers.

    Parameters
    ----------
    exc
        The exception to describe; its ``__traceback__`` supplies the frames.
    canonic
        Optional filename normaliser (``bdb.Bdb.canonic``). Pass it so a frame's
        ``filename`` is spelled exactly as the ``stack``/``paused`` messages
        spell it — that is what lets the pane recognise a traceback frame as a
        live stack frame and make it selectable. Source is still read under the
        original name, which is the one ``linecache`` knows.

    Returns
    -------
    The chain, oldest cause first. Never raises: a formatting failure degrades
    to a single frame-less entry rather than losing the pause.
    """
    try:
        te = traceback.TracebackException.from_exception(exc, lookup_lines=True)
        return _chain(te, canonic or (lambda name: name))
    except Exception:  # noqa: BLE001 — a broken repr must not cost us the pause
        return [{"type": type(exc).__name__, "headline": [str(exc)], "frames": []}]


def _chain(
    te: traceback.TracebackException, canonic: Callable[[str], str]
) -> list[dict[str, Any]]:
    """Walk ``te``'s cause/context links and return the chain oldest-first."""
    entries: list[dict[str, Any]] = []
    seen: set[int] = set()
    while id(te) not in seen:
        seen.add(id(te))
        # Which link (if any) leads further back, and what Python calls it.
        if te.__cause__ is not None:
            nxt, relation = te.__cause__, "cause"
        elif te.__context__ is not None and not te.__suppress_context__:
            nxt, relation = te.__context__, "context"
        else:
            nxt, relation = None, None
        entry: dict[str, Any] = {
            "type": te.exc_type_str,
            "headline": "".join(te.format_exception_only()).splitlines(),
            "frames": [_frame(fs, canonic) for fs in te.stack],
        }
        # `relation` describes how *this* entry follows the one printed before
        # it, which — walking backwards — is the entry we are about to visit.
        if relation is not None:
            entry["relation"] = relation
        entries.append(entry)
        if nxt is None:
            break
        te = nxt
    entries.reverse()
    return entries


def _frame(fs: traceback.FrameSummary, canonic: Callable[[str], str]) -> dict[str, Any]:
    """One traceback frame plus a window of the source around its failing line."""
    lineno = fs.lineno or 0
    # checkcache first: a long-running debuggee may have edited the file since
    # linecache last read it, and a stale window would point at the wrong code.
    linecache.checkcache(fs.filename)
    source = linecache.getlines(fs.filename)
    # `exact` means we read the real file: only then do the anchor columns (which
    # index the *unstripped* line) line up with what we send.
    exact = bool(source) and 1 <= lineno <= len(source)

    if exact:
        first = max(1, lineno - CONTEXT_BEFORE)
        last = min(len(source), lineno + CONTEXT_AFTER)
        lines = [line.rstrip("\n") for line in source[first - 1 : last]]
    else:
        # No file to read (`<string>`, a REPL, a deleted file) — fall back to the
        # single (stripped) line the traceback itself carries, if any.
        first = lineno
        lines = [fs.line] if fs.line else []

    frame: dict[str, Any] = {
        "filename": canonic(fs.filename),
        "lineno": lineno,
        "function": fs.name,
        "lines": lines,
        "first_lineno": first,
    }
    # Multi-line anchors can't be drawn under a single line, so skip those too.
    if (
        exact
        and fs.colno is not None
        and fs.end_colno is not None
        and fs.end_lineno == fs.lineno
    ):
        frame["col"] = fs.colno
        frame["end_col"] = fs.end_colno
    return frame
