"""Process-level settings for judb (``[tool.judb]`` / ``~/.config/judb``).

Deliberately small (docs/PHASE3_PLAN.md B5): judb has exactly two kinds of
preference, and they live in two different places.

* **Process-level options** — the ones that shape how the *debuggee process*
  behaves before any browser exists: whether to open a tab, whether an uncaught
  exception lands in post-mortem, which matplotlib mode figures use. Those are
  what this module resolves.
* **Pure-UI preferences** — the theme, the watch list — stay in the browser's
  ``localStorage``, where they already are. They are unknowable to the Python
  side until a tab connects, and syncing them back would buy a bidirectional
  settings protocol we do not need.

Resolution order, most specific first::

    explicit argument  (e.g. ``set_trace(open_browser=False)``)
    CLI flag           (``python -m judb --no-browser …`` → `configure`)
    project            nearest ``pyproject.toml`` walking up from the cwd, `[tool.judb]`
    user               ``$XDG_CONFIG_HOME/judb/config.toml`` (top-level keys)
    default            the `Settings` field defaults

Only the *nearest* ``pyproject.toml`` is consulted — the first one found going
up is the project root, and a checkout nested inside another project should not
inherit the outer one's debugger settings.

``JUDB_NO_BROWSER`` remains a hard override on top of all of this (see
:meth:`judb.debugger.Debugger.start_server`): it exists so a headless box or a
test harness can suppress the tab without editing anything.

A malformed file, an unknown key or an ill-typed value is **a warning on
stderr, never an exception**. Settings are a convenience; refusing to start the
debugger because a stale key sits in someone's config would be a poor trade.
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from os import environ
from pathlib import Path
from typing import Any, Final

#: Accepted values for `Settings.figure_format`: inline PNG snapshots (what a
#: notebook does), or judb's live WebAgg backend (what ``%matplotlib judb``
#: turns on by hand).
FIGURE_FORMATS: Final = ("png", "interactive")


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved process-level settings.

    Attributes
    ----------
    open_browser
        Whether starting the debugger opens a browser tab. The URL is printed
        either way, so ``false`` suits headless boxes and SSH sessions.
    break_on_exception
        Whether ``python -m judb`` catches an uncaught exception into
        post-mortem instead of letting the process die with a traceback.
    stop_on_entry
        Whether ``python -m judb`` pauses on the target's first line. With
        ``false`` the program simply runs — until a ``breakpoint()``, an
        uncaught exception, or its own end — which is the "run it and catch the
        crash" workflow.
    figure_format
        How matplotlib figures come back: ``"png"`` for inline snapshots, or
        ``"interactive"`` to start with judb's live WebAgg backend already
        selected (equivalent to running ``%matplotlib judb`` in every session).
    """

    open_browser: bool = True
    break_on_exception: bool = True
    stop_on_entry: bool = True
    figure_format: str = "png"


def user_config_path() -> Path:
    """Return the path of the per-user config file.

    Returns
    -------
    ``$XDG_CONFIG_HOME/judb/config.toml``, falling back to
    ``~/.config/judb/config.toml``. Unlike the ``pyproject.toml`` layer, this
    file holds judb's keys at the **top level** — it is judb's own file, so
    there is nothing to namespace them against.
    """
    base = environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "judb" / "config.toml"


def _warn(message: str) -> None:
    """Report a configuration problem without derailing the debuggee."""
    print(f"judb: {message}", file=sys.stderr)


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse ``path`` as TOML, treating any problem as "no settings here"."""
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        _warn(f"ignoring {path}: {exc}")
        return {}
    except tomllib.TOMLDecodeError as exc:
        _warn(f"ignoring {path}: not valid TOML ({exc})")
        return {}


def _project_table(start: Path) -> dict[str, Any]:
    """Return the ``[tool.judb]`` table of the nearest enclosing project.

    Parameters
    ----------
    start
        Directory to start the upward walk from (normally the cwd).

    Returns
    -------
    The ``[tool.judb]`` mapping of the first ``pyproject.toml`` found walking
    up from ``start``, or an empty mapping if there is none (or it has no such
    table). The walk stops at that first file: it *is* the project root.
    """
    for directory in (start, *start.parents):
        candidate = directory / "pyproject.toml"
        if not candidate.is_file():
            continue
        tool = _read_toml(candidate).get("tool")
        if not isinstance(tool, Mapping):
            return {}
        judb = tool.get("judb")
        if judb is None:
            return {}
        if not isinstance(judb, Mapping):
            _warn(f"ignoring {candidate}: [tool.judb] is not a table")
            return {}
        return _validated(judb, source=f"{candidate} [tool.judb]")
    return {}


def _validated(raw: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    """Keep the entries of ``raw`` that are known keys with well-typed values.

    Parameters
    ----------
    raw
        The key/value mapping read from a config file.
    source
        Human-readable origin, used in the warning for a rejected entry.

    Returns
    -------
    The accepted subset, ready to pass to `Settings`.
    """
    accepted: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "figure_format":
            if value not in FIGURE_FORMATS:
                options = ", ".join(repr(fmt) for fmt in FIGURE_FORMATS)
                _warn(f"ignoring figure_format = {value!r} in {source}: use {options}")
                continue
        elif key in ("open_browser", "break_on_exception", "stop_on_entry"):
            if not isinstance(value, bool):
                _warn(f"ignoring {key} = {value!r} in {source}: expected true or false")
                continue
        else:
            _warn(f"ignoring unknown setting {key!r} in {source}")
            continue
        accepted[key] = value
    return accepted


def load_settings(*, cwd: Path | None = None) -> Settings:
    """Resolve settings from the config files, ignoring any cache.

    Parameters
    ----------
    cwd
        Directory the project search starts from; defaults to the process's
        current working directory.

    Returns
    -------
    The merged `Settings` (project layer over user layer over defaults).
    """
    values = _validated(_read_toml(user_config_path()), source=str(user_config_path()))
    values.update(_project_table(Path.cwd() if cwd is None else cwd))
    return Settings(**values)


_cached: Settings | None = None


def settings() -> Settings:
    """Return the process's settings, resolving the files on first use.

    The result is cached: config is read once per process, so a debuggee that
    changes directory mid-run cannot make judb answer differently later on.

    Returns
    -------
    The active `Settings`.
    """
    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached


def configure(**overrides: Any) -> Settings:  # noqa: ANN401 — per-key types vary
    """Resolve the files, apply ``overrides`` on top, and cache the result.

    This is how a command line beats a config file: ``python -m judb`` parses
    its flags and passes the ones that were actually given, so an unspecified
    flag leaves the file's value (or the default) alone.

    Parameters
    ----------
    **overrides
        `Settings` field values to force. Entries whose value is ``None`` are
        skipped, so callers can pass "not specified" straight through.

    Returns
    -------
    The now-cached `Settings`.
    """
    global _cached
    given = {key: value for key, value in overrides.items() if value is not None}
    _cached = replace(load_settings(), **given)
    return _cached


def reset() -> None:
    """Drop the cached settings so the next `settings` call re-reads the files.

    Only tests should need this — a process's configuration does not change
    under it.
    """
    global _cached
    _cached = None
