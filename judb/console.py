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
import pydoc
from collections.abc import Iterator
from types import FrameType
from typing import Any

import matplotlib

# The inline backend renders figures headlessly and feeds them to the display
# publisher via flush_figures(); no GUI / display server required.
matplotlib.use("module://matplotlib_inline.backend_inline")

from IPython.core.displayhook import DisplayHook
from IPython.core.displaypub import DisplayPublisher
from IPython.core.error import TryNext
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.pylabtools import select_figure_formats
from matplotlib_inline.backend_inline import flush_figures

from . import mpl_backend
from .protocol import CellResult, Output

# The buffer the capture classes append to while a cell runs. IPython owns the
# lifecycle of the hook/publisher instances, so a module-level buffer (rather
# than per-instance state) is the tidy seam; cell execution is serialized on the
# debuggee thread, so a single active buffer is sufficient.
_capture: list[Output] | None = None

# Caps for lazy variable inspection (``inspect``): how many children of a
# sequence/dict to list, and how long a child's one-line summary repr may get.
_MAX_CHILDREN = 200
_SUMMARY_CAP = 160

# A path step from the frontend is a ``[kind, key]`` pair (JSON list). ``kind`` is
# how to descend from the parent object; the first step is always a ``name``.
PathStep = tuple[str, Any]


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


class _InlineShell(InteractiveShell):
    """An embedded shell whose only "GUI" is the inline backend.

    The base ``InteractiveShell.enable_gui`` is an abstract stub that raises
    ``NotImplementedError`` — real front-ends (terminal, zmq/ipykernel) implement
    it to drive a GUI event loop. judb's console renders everything inline (no
    event loop in the paused debuggee thread), so ``%matplotlib inline`` — which
    routes through ``enable_gui('inline')`` — would otherwise blow up. Treat the
    inline / no-GUI cases as a no-op; a real GUI backend genuinely isn't
    supported here, so keep the clear error for those.
    """

    def enable_gui(self, gui: str | None = None) -> None:
        if gui not in (None, "inline"):
            raise NotImplementedError(
                f"judb's console only supports the inline matplotlib backend, not {gui!r}"
            )


def _capture_pager(
    shell: InteractiveShell,
    data: Any,  # noqa: ANN401 — a mime bundle or plain string from the pager
    start: int = 0,
    screen_lines: int = 0,
) -> None:
    """IPython ``show_in_pager`` hook: capture ``obj?`` / ``obj??`` introspection.

    Without this, IPython pages the docstring/source through the *system pager*
    (``less``), which writes to the debuggee's controlling terminal — off in the
    debuggee's stdout, not in judb. Appending it to ``_capture`` turns it into a
    normal ``display_data`` output rendered in the console instead. When no cell
    is running (no active buffer) we defer to the default pager via ``TryNext``.
    """
    if _capture is None:
        raise TryNext
    bundle = dict(data) if isinstance(data, dict) else {"text/plain": str(data)}
    if bundle:
        _capture.append(Output("display_data", bundle))


def _pydoc_pager(text: str, title: str = "") -> None:
    """Replacement for ``pydoc.pager`` so ``help(obj)`` renders inline.

    ``help()`` reaches the terminal by a *different* route than IPython's ``?``:
    it calls ``pydoc.pager``, which on first use caches a concrete pager
    (``less``) keyed on the *then-current* ``sys.stdout``. So a ``help()`` run in
    the debuggee's terminal before pausing would pin the ``less`` pager, and every
    later in-console ``help()`` would page to that terminal despite our stdout
    redirect. Replacing ``pydoc.pager`` outright sidesteps the cache: while a cell
    runs we capture the text (stripping pydoc's ``\\b`` overstrike bolding via
    ``pydoc.plain``); otherwise we page normally without re-clobbering ourselves.
    """
    if _capture is None:
        pydoc.getpager()(text, title)
        return
    clean = pydoc.plain(text)
    if clean:
        _capture.append(Output("display_data", {"text/plain": clean}))


class Console:
    """A reusable embedded IPython console for in-frame cell execution."""

    def __init__(self) -> None:
        # Use the singleton so that display()/get_ipython() route here, which is
        # what makes flush_figures() send figures to our display publisher.
        # `_InlineShell.instance()` makes our subclass the process singleton, so
        # `get_ipython()` / `InteractiveShell.instance()` route here too — which
        # is what lets `%matplotlib inline` (and `display()`, `flush_figures()`)
        # find this shell.
        self.shell: InteractiveShell = _InlineShell.instance(
            displayhook_class=_CapturingDisplayHook,
            display_pub_class=_CapturingDisplayPublisher,
        )
        matplotlib.interactive(True)
        select_figure_formats(self.shell, {"png"})
        # Deterministic, fast completions: the rlcompleter-style path returns the
        # fragment being replaced + full replacements (what `complete` relies on),
        # whereas jedi is slower and, without real type info for frame locals,
        # frequently returns nothing here.
        self.shell.Completer.use_jedi = False
        # Capture `obj?` / `obj??` introspection inline instead of letting it
        # escape to the debuggee's terminal pager (see _capture_pager).
        self.shell.set_hook("show_in_pager", _capture_pager)
        # Same intent for `help(obj)`, which pages via pydoc, not IPython
        # (see _pydoc_pager). Process-global, deliberately: judb owns the
        # debuggee's console experience. (setattr: pydoc.pager is a typed
        # module attribute, so a plain rebind trips the type checker.)
        setattr(pydoc, "pager", _pydoc_pager)  # noqa: B010
        # Frame names currently injected into user_ns, and the base values they
        # shadow (so switching frames doesn't leak names or clobber IPython's own
        # entries / the user's scratch). See _sync_frame_namespace.
        self._injected: set[str] = set()
        self._shadowed: dict[str, Any] = {}

    def _flush_figures(self) -> None:
        """Turn figures a cell created into outputs. Under judb's interactive
        backend (``%matplotlib judb``) each new figure becomes a live WebAgg
        canvas (a mount notice); otherwise inline PNGs (``%matplotlib inline``)."""
        if mpl_backend.is_active():
            for fig_id in mpl_backend.announce_new_figures():
                if _capture is not None:
                    _capture.append(
                        Output(
                            "display_data",
                            {
                                mpl_backend.WEBAGG_MIME: {"id": fig_id},
                                "text/plain": f"<interactive matplotlib figure #{fig_id}>",
                            },
                        )
                    )
        else:
            flush_figures()

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
            self._sync_frame_namespace(frame)

        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = self.shell.run_cell(code, store_history=True)
                # Turn any figures the cell created into outputs (inline PNGs, or
                # a live interactive canvas under `%matplotlib_interactive`).
                self._flush_figures()
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

    def _sync_frame_namespace(self, frame: FrameType) -> None:
        """Make user_ns reflect *exactly* ``frame``'s globals + locals.

        A previous frame's names must not leak (selecting an outer frame that
        lacks ``df`` should not still resolve ``df``), yet we must not disturb
        IPython's own entries (``display``, ``get_ipython``, ...) or names the
        user bound in a cell (notebook-style scratch persists). So we track the
        frame names we injected and the base values they shadowed, undo that,
        then inject the new frame — remembering what it shadows in turn.
        """
        ns = self.shell.user_ns
        for key in self._injected:
            if key in self._shadowed:
                ns[key] = self._shadowed[key]
            else:
                ns.pop(key, None)

        new_vars = {**frame.f_globals, **frame.f_locals}
        self._shadowed = {k: ns[k] for k in new_vars if k in ns}
        ns.update(new_vars)
        self._injected = set(new_vars)

    def evaluate(self, code: str, frame: FrameType | None = None) -> Any:  # noqa: ANN401
        """Convenience for tests: run ``code`` and return its ``text/plain``."""
        return self.run_cell(code, frame).first_of("text/plain")

    # --- tab completion ---------------------------------------------------

    def complete(
        self, code: str, cursor: int, frame: FrameType | None = None
    ) -> tuple[int, list[str]]:
        """Complete ``code`` at offset ``cursor`` against the frame's namespace.

        Returns ``(replace_from, matches)`` where ``matches`` are full
        replacements for the text spanning ``[replace_from, cursor)`` — the shape
        CodeMirror's autocomplete wants. Completion runs against the *current*
        line only (IPython's completer is line-oriented), so ``replace_from`` is
        an absolute offset into ``code``.
        """
        if frame is not None:
            self._sync_frame_namespace(frame)
        cursor = max(0, min(cursor, len(code)))
        line_start = code.rfind("\n", 0, cursor) + 1
        line = code[line_start:cursor]
        fragment, matches = self.shell.Completer.complete(
            text=None, line_buffer=line, cursor_pos=len(line)
        )
        return cursor - len(fragment), list(matches)

    # --- lazy variable inspection -----------------------------------------

    def inspect(self, frame: FrameType, path: list[Any]) -> dict[str, Any]:
        """Resolve ``path`` against ``frame`` and return its repr + children.

        ``path`` starts with a ``("name", <local>)`` step and descends by
        attribute / item / index. The returned ``repr`` is a Jupyter mime bundle
        (so a DataFrame renders as an HTML table in the same ``<Output>`` the
        console uses); ``children`` are one level deep, each carrying the full
        path to expand it in turn. Resolution reads the frame's real objects
        directly — it never runs user code or touches the shell namespace.
        """
        obj = self._resolve(frame, path)
        return {
            "repr": self._format(obj),
            "children": self._children_of(obj, path),
        }

    @staticmethod
    def _resolve(frame: FrameType, path: list[Any]) -> Any:  # noqa: ANN401
        if not path:
            raise ValueError("empty path")
        ns = {**frame.f_globals, **frame.f_locals}
        (kind, key), *rest = path
        if kind != "name":
            raise ValueError(f"path must start with a name, not {kind!r}")
        if key not in ns:
            raise KeyError(key)
        obj = ns[key]
        for step in rest:
            obj = Console._step(obj, tuple(step))
        return obj

    @staticmethod
    def _step(obj: Any, step: PathStep) -> Any:  # noqa: ANN401
        kind, key = step
        if kind == "attr":
            return getattr(obj, key)
        if kind in ("item", "index"):
            return obj[key]
        raise ValueError(f"bad path step kind: {kind!r}")

    def _format(self, obj: Any) -> dict[str, Any]:  # noqa: ANN401
        formatter = self.shell.display_formatter
        if formatter is None:  # pragma: no cover - always set on a live shell
            return {"text/plain": self._short_repr(obj)}
        # Inspect passively. Some objects (e.g. plotly figures) render via
        # `_ipython_display_`, which would hijack `format()` — returning no mime
        # bundle and instead *displaying* themselves (a side effect that lands
        # nowhere during inspection, since `_capture` is None). Disabling that
        # formatter makes `format()` fall through to the object's
        # `_repr_mimebundle_`/`_repr_*_`, yielding the same rich bundle the
        # console shows so the Variables tree can render it too.
        ipython_display = formatter.ipython_display_formatter
        was_enabled = ipython_display.enabled
        ipython_display.enabled = False
        try:
            data, _md = formatter.format(obj)
        finally:
            ipython_display.enabled = was_enabled
        data = dict(data)
        data.setdefault("text/plain", self._short_repr(obj))
        return data

    def _children_of(self, obj: Any, parent: list[Any]) -> list[dict[str, Any]]:  # noqa: ANN401
        children: list[dict[str, Any]] = []
        for display, step in self._child_steps(obj):
            try:
                child = self._step(obj, step)
            except Exception:  # noqa: BLE001, S112 — a broken __getitem__/property is skippable
                continue
            children.append(
                {
                    "key": display,
                    "path": [*parent, list(step)],
                    "summary": self._short_repr(child),
                    "expandable": self._is_expandable(child),
                }
            )
        return children

    @staticmethod
    def _child_steps(obj: Any) -> Iterator[tuple[str, PathStep]]:  # noqa: ANN401
        if isinstance(obj, dict):
            for k in list(obj)[:_MAX_CHILDREN]:
                # Only JSON-round-trippable keys survive the wire as-is.
                if type(k) is str or type(k) is int:
                    yield repr(k), ("item", k)
            return
        if isinstance(obj, (list, tuple)):
            for i in range(min(len(obj), _MAX_CHILDREN)):
                yield str(i), ("index", i)
            return
        attrs = getattr(obj, "__dict__", None)
        if isinstance(attrs, dict):
            for name in sorted(attrs):
                if not name.startswith("_"):
                    yield name, ("attr", name)

    @staticmethod
    def _is_expandable(obj: Any) -> bool:  # noqa: ANN401
        if isinstance(obj, dict):
            return any(type(k) is str or type(k) is int for k in obj)
        if isinstance(obj, (list, tuple)):
            return len(obj) > 0
        attrs = getattr(obj, "__dict__", None)
        return isinstance(attrs, dict) and any(not k.startswith("_") for k in attrs)

    @staticmethod
    def _short_repr(obj: Any) -> str:  # noqa: ANN401
        try:
            text = repr(obj)
        except Exception as exc:  # noqa: BLE001 — a broken __repr__ must not break inspection
            text = f"<repr failed: {type(exc).__name__}: {exc}>"
        text = " ".join(text.split())
        if len(text) > _SUMMARY_CAP:
            text = text[:_SUMMARY_CAP] + "…"
        return f"{type(obj).__name__}  {text}"
