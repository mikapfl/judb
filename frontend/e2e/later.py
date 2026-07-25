"""A module the e2e debuggee only reaches *after* the pause.

It exists so the browser can open a file no frame is in yet, set a breakpoint in
it, and stop there on `continue` — the multi-file navigation criterion. Kept
import-free and trivial: the point is only that its code has not run when the
debugger first pauses.
"""


def finish(value: float) -> str:
    summary = f"LATER_FRAME {value:.1f}"
    return summary
