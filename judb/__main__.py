"""``python -m judb script.py [args...]`` — run a script or module under judb.

Mirrors ``pdb``'s runner: the target executes in a fresh ``__main__`` namespace
with the debugger tracing it, and stops on entry (the first executable line) so
the browser UI opens and you can set breakpoints / step from the top.

We resolve the target to a **code object** and hand *that* to ``Bdb.run`` (rather
than pdb's ``exec(compile(...))`` string wrapper), so the first traced frame is
the target itself — no synthetic ``<string>`` frame to skip, hence no
``_wait_for_mainpyfile`` dance.

Scope (Phase 3 / Wave A, see PHASE3_PLAN.md A2):
  * ``python -m judb script.py [args]`` and ``python -m judb -m pkg.mod [args]``.
  * ``-c`` is deliberately absent: pdb's ``-c`` takes *debugger commands*, which
    judb drives from the browser instead.
  * Default-stop behavior is **stop-on-entry** (resolved open decision #3).
  * The process exits when the target finishes (no post-run inspection prompt
    yet) — a follow-up for A3/B2.
"""

import builtins
import io
import sys
from pathlib import Path
from types import CodeType

from .debugger import Debugger

_USAGE = "usage: python -m judb [-m module | script.py] [args...]"


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m judb``.

    ``argv`` defaults to ``sys.argv[1:]``: either ``script.py [args]`` or
    ``-m module [args]``, where the trailing arguments belong to the target.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE, file=sys.stderr)
        raise SystemExit(0 if args[:1] in (["-h"], ["--help"]) else 2)

    if args[0] == "-c":
        print(
            "judb: '-c' is not supported — pdb's -c passes debugger commands, "
            f"which judb drives from the browser.\n{_USAGE}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if args[0] == "-m":
        if len(args) < 2:
            print(f"judb: '-m' needs a module name\n{_USAGE}", file=sys.stderr)
            raise SystemExit(2)
        _run_module(args[1], args[2:], open_browser=True)
        return

    target = Path(args[0])
    if not target.is_file():
        print(f"judb: error: no such file: {args[0]}", file=sys.stderr)
        raise SystemExit(2)
    _run_script(target, args[1:], open_browser=True)


def _run_script(script: Path, args: list[str], *, open_browser: bool = True) -> None:
    """Run ``script`` under a fresh :class:`Debugger`, stopping on entry."""
    script_path = str(script)
    with io.open_code(script_path) as fp:
        code = compile(fp.read(), script_path, "exec")
    _run_code(
        code,
        # Python's usual script contract: sys.path[0] is the script's directory.
        sys_path_entry=str(script.resolve().parent),
        argv=[script_path, *args],
        main_globals={
            "__name__": "__main__",
            "__file__": script_path,
            "__spec__": None,
        },
        open_browser=open_browser,
    )


def _run_module(module: str, args: list[str], *, open_browser: bool = True) -> None:
    """Run ``module`` as ``__main__`` under a fresh :class:`Debugger`."""
    import runpy

    # `runpy._get_module_details` is private, but it is exactly what `pdb -m`
    # uses: it resolves a module to its code object *without* executing it, so
    # we can hand that code to Bdb.run and stop on its first line.
    try:
        _, spec, code = runpy._get_module_details(module)  # ty: ignore[unresolved-attribute]
    except ImportError as exc:
        print(f"judb: error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    _run_code(
        code,
        # `python -m` semantics: the current directory leads sys.path.
        sys_path_entry=str(Path.cwd()),
        argv=[code.co_filename, *args],
        main_globals={
            "__name__": "__main__",
            "__file__": code.co_filename,
            "__package__": spec.parent,
            "__loader__": spec.loader,
            "__spec__": spec,
        },
        open_browser=open_browser,
    )


def _run_code(
    code: CodeType,
    *,
    sys_path_entry: str,
    argv: list[str],
    main_globals: dict[str, object],
    open_browser: bool = True,
) -> None:
    """Execute a resolved ``code`` object as ``__main__`` under the debugger.

    Factored out of the script/module paths (and so tests can drive it with
    ``open_browser=False``). The debuggee sees the arguments as if it had been
    invoked directly, in a namespace that does not inherit judb's globals.
    """
    sys.argv = list(argv)
    sys.path.insert(0, sys_path_entry)

    import __main__

    # Reuse the real `__main__` dict (not a throwaway one) so the debuggee's
    # module *is* sys.modules["__main__"] — pickling, `if __name__ ==
    # "__main__"`, and multiprocessing all depend on that. Note this clears the
    # globals of whichever module is currently `__main__`; see the re-entry
    # dance at the bottom of this file for why that is safe.
    namespace = __main__.__dict__
    namespace.clear()
    namespace.update({"__builtins__": builtins, **main_globals})

    dbg = Debugger()
    dbg.start_server(open_browser=open_browser)
    # Bdb.run traces the exec: it stops at the target's first executable line
    # (stop-on-entry), then the UI drives stepping/continue as usual. BdbQuit
    # (from the UI's "quit") is swallowed by Bdb.run.
    dbg.run(code, namespace, namespace)


if __name__ == "__main__":
    # `python -m judb` executes *this file* as `__main__`, so our module globals
    # are `__main__.__dict__` — the very dict `_run_code` clears to hand the
    # debuggee a fresh namespace. That would pull `Debugger`, `sys`, … out from
    # under the running call. Re-enter through an imported copy of ourselves,
    # whose globals are a separate dict and so survive the clear. (`pdb` does
    # the same dance for the same reason.)
    # (ty can't model a module importing itself under its non-__main__ name;
    # the import is resolved normally at runtime and covered by tests.)
    from judb.__main__ import main as _main  # ty: ignore[unresolved-import]

    _main()
