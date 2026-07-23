"""``python -m judb script.py [args...]`` — run a script under judb.

Mirrors ``pdb``'s script runner: the target executes in a fresh ``__main__``
namespace with the debugger tracing it, and stops on entry (the first executable
line) so the browser UI opens and you can set breakpoints / step from the top.

We compile the script to a code object and hand *that* to ``Bdb.run`` (rather
than pdb's ``exec(compile(...))`` string wrapper), so the first traced frame is
the script itself — no synthetic ``<string>`` frame to skip, hence no
``_wait_for_mainpyfile`` dance.

Scope (Phase 3 / Wave A scaffold, see PHASE3_PLAN.md A2):
  * ``python -m judb script.py [args]`` — implemented.
  * ``-m module`` / ``-c cmd`` forms — not yet (rejected with a clear message).
  * Default-stop behavior is **stop-on-entry**. Whether ``-m judb`` should
    instead run-to-first-breakpoint/exception is open decision #3; the exception
    half needs break-on-exception (Wave B / B2) first, so revisit then.
  * The process exits when the script finishes (no post-run inspection prompt
    yet) — a follow-up for A3/B2.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from .debugger import Debugger

_USAGE = "usage: python -m judb script.py [args...]"


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m judb``.

    ``argv`` defaults to ``sys.argv[1:]``; the first element is the target
    script, the rest are the script's own arguments.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE, file=sys.stderr)
        raise SystemExit(0 if args[:1] in (["-h"], ["--help"]) else 2)

    if args[0] in ("-m", "-c"):
        print(
            f"judb: {args[0]!r} is not supported yet — pass a script path "
            f"(see PHASE3_PLAN.md A2).\n{_USAGE}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    target = Path(args[0])
    if not target.is_file():
        print(f"judb: error: no such file: {args[0]}", file=sys.stderr)
        raise SystemExit(2)

    _run_script(target, args, open_browser=True)


def _run_script(script: Path, argv: list[str], *, open_browser: bool = True) -> None:
    """Run ``script`` under a fresh :class:`Debugger`, stopping on entry.

    Factored out of :func:`main` so tests can drive it with
    ``open_browser=False`` and feed the debugger's queues on another thread.
    """
    script_path = str(script)

    # The debuggee sees the arguments as if invoked directly: argv[0] is the
    # script, and sys.path[0] is its directory (Python's usual script contract).
    sys.argv = list(argv)
    sys.path.insert(0, str(script.resolve().parent))

    # A fresh __main__ namespace so the script doesn't inherit judb's globals.
    import __main__

    main_globals = __main__.__dict__
    main_globals.clear()
    main_globals.update(
        {
            "__name__": "__main__",
            "__file__": script_path,
            "__builtins__": __builtins__,
            "__spec__": None,
        }
    )

    with io.open_code(script_path) as fp:
        code = compile(fp.read(), script_path, "exec")

    dbg = Debugger()
    dbg.start_server(open_browser=open_browser)
    # Bdb.run traces the exec: it stops at the script's first executable line
    # (stop-on-entry), then the UI drives stepping/continue as usual. BdbQuit
    # (from the UI's "quit") is swallowed by Bdb.run.
    dbg.run(code, main_globals, main_globals)


if __name__ == "__main__":
    main()
