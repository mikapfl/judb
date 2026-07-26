"""``python -m judb script.py [args...]`` — run a script or module under judb.

Mirrors ``pdb``'s runner: the target executes in a fresh ``__main__`` namespace
with the debugger tracing it, and stops on entry (the first executable line) so
the browser UI opens and you can set breakpoints / step from the top.

We resolve the target to a **code object** and hand *that* to ``Bdb.run`` (rather
than pdb's ``exec(compile(...))`` string wrapper), so the first traced frame is
the target itself — no synthetic ``<string>`` frame to skip, hence no
``_wait_for_mainpyfile`` dance.

Scope (Phase 3 / Wave A, see docs/PHASE3_PLAN.md A2):
  * ``python -m judb script.py [args]`` and ``python -m judb -m pkg.mod [args]``.
  * ``-c`` is deliberately absent: pdb's ``-c`` takes *debugger commands*, which
    judb drives from the browser instead.
  * Default-stop behavior is **stop-on-entry** (resolved open decision #3);
    ``stop_on_entry = false`` / ``--no-stop-on-entry`` starts the program
    instead, so it runs until a ``breakpoint()`` or a crash.
  * The process exits when the target finishes; if it *crashes* (an uncaught
    exception), it drops into post-mortem first so the browser can inspect the
    failing frame, then exits non-zero (Phase 3 / Wave B, B2) — unless
    ``break_on_exception`` is off, in which case the crash propagates as it
    would have without judb.
  * judb's own options come *before* the target and are the command-line face
    of :mod:`judb.config`; everything after the target belongs to the target
    (Phase 3 / Wave B, B5).
"""

import builtins
import io
import sys
from pathlib import Path
from types import CodeType
from typing import Any, NoReturn

from . import config
from .debugger import Debugger

_USAGE = """\
usage: python -m judb [judb options] [-m module | script.py] [args...]

judb options (they override ~/.config/judb/config.toml and [tool.judb]):
  --browser, --no-browser          open a browser tab on start (default: open)
  --stop-on-entry,                 pause on the target's first line, or just
  --no-stop-on-entry               run it (default: pause)
  --break-on-exception,            stop in post-mortem on an uncaught exception
  --no-break-on-exception          (default: stop)
  --figure-format {png,interactive}
                                   inline PNG figures, or live `%matplotlib
                                   judb` ones (default: png)
  -h, --help                       show this message"""


# judb's own boolean options: flag → the `judb.config.Settings` field it sets
# and the value it sets it to.
_FLAGS: dict[str, tuple[str, bool]] = {
    "--browser": ("open_browser", True),
    "--no-browser": ("open_browser", False),
    "--break-on-exception": ("break_on_exception", True),
    "--no-break-on-exception": ("break_on_exception", False),
    "--stop-on-entry": ("stop_on_entry", True),
    "--no-stop-on-entry": ("stop_on_entry", False),
}


def _fail(message: str) -> NoReturn:
    """Report a command-line error the way a CLI should, then exit 2."""
    print(f"judb: error: {message}\n\n{_USAGE}", file=sys.stderr)
    raise SystemExit(2)


def _parse_options(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Split judb's own leading options off the target and its arguments.

    Parsing stops at the first argument that is not one of judb's options (the
    script, ``-m``, or an explicit ``--``), so the target's own flags are never
    consumed — ``python -m judb train.py --no-browser`` passes ``--no-browser``
    to *train.py*, which is the only reading that lets a target have flags of
    the same name.

    Parameters
    ----------
    argv
        The arguments to ``python -m judb``, without the program name.

    Returns
    -------
    The settings overrides the options ask for, and the remaining arguments
    (the target and everything after it).
    """
    overrides: dict[str, Any] = {}
    args = list(argv)
    while args:
        option = args[0]
        if option in ("-h", "--help"):
            print(_USAGE)
            raise SystemExit(0)
        if option == "--":  # explicit end of judb's options
            args.pop(0)
            break
        if not option.startswith("--"):
            break
        args.pop(0)
        name, has_value, inline = option.partition("=")
        if name == "--figure-format":
            if not has_value:
                if not args:
                    _fail("--figure-format needs a value")
                inline = args.pop(0)
            if inline not in config.FIGURE_FORMATS:
                choices = ", ".join(config.FIGURE_FORMATS)
                _fail(f"--figure-format takes one of {choices}, not {inline!r}")
            overrides["figure_format"] = inline
            continue
        if name not in _FLAGS:
            _fail(f"unknown option: {name}")
        if has_value:
            _fail(f"{name} takes no value")
        field, value = _FLAGS[name]
        overrides[field] = value
    return overrides, args


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m judb``.

    Parameters
    ----------
    argv
        The command-line arguments, defaulting to ``sys.argv[1:]``: judb's own
        options (see ``--help``), then either ``script.py [args]`` or
        ``-m module [args]``, where the trailing arguments belong to the target.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    overrides, args = _parse_options(args)

    if not args:
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)

    # Resolve settings here, at the top: the config files are found relative to
    # the *invocation's* cwd, and the target is free to chdir once it runs.
    config.configure(**overrides)

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
        _run_module(args[1], args[2:])
        return

    target = Path(args[0])
    if not target.is_file():
        print(f"judb: error: no such file: {args[0]}", file=sys.stderr)
        raise SystemExit(2)
    _run_script(target, args[1:])


def _run_script(
    script: Path, args: list[str], *, open_browser: bool | None = None
) -> None:
    """Run ``script`` under a fresh :class:`Debugger` (stopping on entry by
    default — see the ``stop_on_entry`` setting).

    Parameters
    ----------
    script
        Path to the script to run.
    args
        The target's own command-line arguments (``sys.argv[1:]`` for it).
    open_browser
        Whether to open a browser tab on start; ``None`` follows the
        configured ``open_browser`` setting.
    """
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


def _run_module(
    module: str, args: list[str], *, open_browser: bool | None = None
) -> None:
    """Run ``module`` as ``__main__`` under a fresh :class:`Debugger`.

    Parameters
    ----------
    module
        The importable module name to run (``python -m judb -m module``).
    args
        The target's own command-line arguments.
    open_browser
        Whether to open a browser tab on start; ``None`` follows the
        configured ``open_browser`` setting.
    """
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
    open_browser: bool | None = None,
) -> None:
    """Execute a resolved ``code`` object as ``__main__`` under the debugger.

    Factored out of the script/module paths (and so tests can drive it with
    ``open_browser=False``). The debuggee sees the arguments as if it had been
    invoked directly, in a namespace that does not inherit judb's globals.

    Parameters
    ----------
    code
        The resolved code object to execute as ``__main__``.
    sys_path_entry
        The directory to prepend to ``sys.path`` (the script's directory, or the
        cwd for ``-m``).
    argv
        The ``sys.argv`` the debuggee should see.
    main_globals
        The globals to seed the fresh ``__main__`` namespace with.
    open_browser
        Whether to open a browser tab on start; ``None`` follows the
        configured ``open_browser`` setting.
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
    if not config.settings().stop_on_entry:
        # Start the program instead of parking on its first line; it then runs
        # until a `breakpoint()`, a crash, or its own end. The tab is already
        # open and shows "running…", so there is somewhere to land.
        dbg.skip_stop_on_entry()
    # Bdb.run traces the exec: it stops at the target's first executable line
    # (stop-on-entry), then the UI drives stepping/continue as usual. BdbQuit
    # (from the UI's "quit") is swallowed by Bdb.run; any *other* exception the
    # debuggee raises past its own code propagates out here.
    try:
        dbg.run(code, namespace, namespace)
    except (SystemExit, KeyboardInterrupt):
        # The debuggee's own exit / a Ctrl+C: honour it, don't treat as a crash.
        raise
    except BaseException as exc:  # noqa: BLE001 — the debuggee crashed; catch it
        # Land the browser on the failing frame so the crash can be inspected in
        # place (this is `-m judb`'s catch-the-crash workflow; see
        # docs/PHASE3_PLAN.md B2), instead of the process dying with only a
        # terminal traceback.
        #
        # Either way, trim judb's own runner frames off the top of the traceback
        # (the `_run_code`/`bdb.run` exec plumbing) so what the user sees — the
        # post-mortem stack, or the printed traceback below — starts at the
        # debuggee's own module frame.
        tb = exc.__traceback__
        while tb is not None and tb.tb_frame.f_code is not code:
            tb = tb.tb_next
        if tb is not None:
            exc = exc.with_traceback(tb)
        if not config.settings().break_on_exception:
            # Opted out of post-mortem: report the crash the way an undebugged
            # run would have, so judb costs an unattended run nothing.
            import traceback

            traceback.print_exception(exc)
            raise SystemExit(1) from None
        dbg.post_mortem(exc)
        # Preserve the failure signal for the shell/CI once inspection is done;
        # the browser already showed the traceback, so exit quietly (no second
        # dump to the terminal).
        raise SystemExit(1) from None


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
