"""Saved debug cells: the percent-format store behind Phase 3a.

Three tiers, one format (docs/PHASE3A_PLAN.md §2, §6)::

    ~/.config/judb/cells.py                   user library    (authored; may auto-run)
    <project root>/.judb/cells.py             project library (committed; never auto-runs)
    ~/.local/state/judb/notebooks/<slug>.py   the working notebook (auto-saved)
    ~/.local/state/judb/notebooks/<slug>.toml watches + active tab

Everything is stored as **percent cells** — the ``# %%`` convention jupytext,
VS Code, Spyder and PyCharm all speak:

.. code-block:: python

    # %% describe the frame at="etl/clean.py::Cleaner.run"
    df.describe()

    # %% imports tags=["setup"]
    import numpy as np

The marker line is ``# %% <name> key=value…``: free text before the first
``key=`` is the cell's name, and the rest is metadata. That buys three things a
JSON or TOML store would not: the committed tier diffs like the Python it is, the
files open as cells in the user's own editor, and everything else that reads
Python (linters, ``grep``, syntax highlighting) still works. Metadata keys judb
does not know are **preserved verbatim**, so sharing a file with another tool
never eats that tool's metadata.

A cell may carry a **location key** (``at=``) saying where it belongs, at any of
three specificities::

    at="etl/clean.py::Cleaner.run"   only in that function
    at="etl/clean.py"               anywhere in that file
    (absent)                        always

Paths in a key are relative to the project root, because the committed tier is
*for* other people's checkouts, where an absolute path means nothing.

This module is pure file handling: no frame is touched (except to *read* a
location off one), nothing is executed. That is what lets the server thread own
it — see docs/PHASE3A_PLAN.md §8.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Final

from . import config

#: Start of a cell marker line. Recognised only at column 0, so a ``# %%``
#: inside a string or an indented comment does not split a cell.
MARKER: Final = "# %%"

#: ``key=`` in a marker line's metadata. The first match also ends the name.
_KEY_RE: Final = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=")

#: Cells tagged this way in the *user's own* library run themselves once per
#: session. Nothing from the project library ever does (PHASE3A_PLAN.md §5).
SETUP_TAG: Final = "setup"


@dataclass(frozen=True, slots=True)
class Cell:
    """One saved cell.

    Attributes
    ----------
    code
        The cell source, without the marker line and without a trailing newline.
    name
        The free-text name from the marker line (may be empty).
    at
        The location key this cell belongs to, or ``""`` for "always available".
    tags
        Notebook-style tags; only `SETUP_TAG` means anything to judb.
    extra
        Metadata keys judb does not know, as ``(key, raw value)`` pairs, kept so
        a round-trip through judb never drops another tool's metadata.
    """

    code: str
    name: str = ""
    at: str = ""
    tags: tuple[str, ...] = ()
    extra: tuple[tuple[str, str], ...] = ()

    def to_wire(self) -> dict[str, Any]:
        """Return the JSON shape the browser sees (mirrored in ``protocol.ts``)."""
        return {
            "code": self.code,
            "name": self.name,
            "at": self.at,
            "tags": list(self.tags),
        }

    @classmethod
    def from_wire(cls, data: object) -> Cell | None:
        """Rebuild a cell from the browser's JSON, or ``None`` if it is malformed.

        Parameters
        ----------
        data
            One entry of a ``save_notebook`` / ``save_cells`` payload.

        Returns
        -------
        The `Cell`, or ``None`` when the entry is not a mapping with string
        ``code`` — the browser is trusted, but a malformed message must not take
        the whole save down with it.
        """
        if not isinstance(data, dict):
            return None
        code = data.get("code")
        if not isinstance(code, str):
            return None
        tags = data.get("tags")
        return cls(
            code=code.rstrip("\n"),
            name=_one_line(data.get("name")),
            at=_one_line(data.get("at")),
            tags=tuple(t for t in tags if isinstance(t, str))
            if isinstance(tags, list)
            else (),
        )


def _one_line(value: object) -> str:
    """Return ``value`` as a single-line string (marker lines are one line)."""
    return " ".join(value.split()) if isinstance(value, str) else ""


# --- the percent format ----------------------------------------------------


def _parse_value(raw: str) -> list[str] | str:
    """Decode one metadata value: a list, a quoted string, or a bare token."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        # Only a flat list of strings is meaningful here (`tags`); anything
        # nested is dropped rather than guessed at.
        parts = (_parse_value(part) for part in inner.split(","))
        return [part for part in parts if isinstance(part, str)]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _parse_marker(
    line: str,
) -> tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Split a marker line into ``(name, at, tags, extra)``.

    Parameters
    ----------
    line
        The full marker line, starting with `MARKER`.

    Returns
    -------
    The cell's name, its location key, its tags, and every metadata key judb
    does not know, each with its value's raw source text.
    """
    rest = line[len(MARKER) :].strip()
    matches = list(_KEY_RE.finditer(rest))
    name = rest[: matches[0].start()].strip() if matches else rest
    # A jupytext cell type (`[markdown]`, `[raw]`) sits in the name slot; keep
    # it out of the name rather than showing a tab called "[markdown]".
    name = re.sub(r"\[[a-z]+\]", "", name).strip()

    at = ""
    tags: list[str] = []
    extra: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
        key = match.group(1)
        raw = rest[match.end() : end].strip()
        if key == "at":
            value = _parse_value(raw)
            at = value if isinstance(value, str) else ""
        elif key == "tags":
            value = _parse_value(raw)
            tags = (
                [t for t in value if isinstance(t, str)]
                if isinstance(value, list)
                else []
            )
        else:
            extra.append((key, raw))
    return name, at, tuple(tags), tuple(extra)


def parse(text: str) -> list[Cell]:
    """Parse percent-format ``text`` into cells.

    Content before the first marker is a header: dropped when it is only
    comments and blank lines (judb writes one), kept as an unnamed first cell
    otherwise, so a jupytext file whose first cell has no marker still loads.

    Parameters
    ----------
    text
        The file's contents.

    Returns
    -------
    The cells, in file order. Empty cells are dropped — a marker with nothing
    under it carries no information a name could not.
    """
    cells: list[Cell] = []
    marker: str | None = None
    body: list[str] = []

    def flush() -> None:
        code = "\n".join(body).strip("\n")
        if not code:
            return
        if marker is None:
            cells.append(Cell(code=code))
            return
        name, at, tags, extra = _parse_marker(marker)
        cells.append(Cell(code=code, name=name, at=at, tags=tags, extra=extra))

    for line in text.splitlines():
        if line.startswith(MARKER):
            # Content before the *first* marker is only a cell if it is real
            # code; judb's own header comment is not.
            if marker is not None or not _is_header("\n".join(body)):
                flush()
            marker, body = line, []
            continue
        body.append(line)
    flush()
    return cells


def _is_header(text: str) -> bool:
    """Whether pre-marker ``text`` is only comments and blank lines."""
    return all(
        not line.strip() or line.lstrip().startswith("#") for line in text.splitlines()
    )


def dumps(cells: list[Cell], *, header: str = "") -> str:
    """Render ``cells`` as percent-format source.

    Parameters
    ----------
    cells
        The cells to write, in order.
    header
        An optional comment block for the top of the file (each line is emitted
        as written; callers pass ``#``-prefixed lines).

    Returns
    -------
    The file's contents, ending in a newline.
    """
    chunks: list[str] = [header.rstrip("\n") + "\n\n"] if header else []
    for cell in cells:
        marker = MARKER
        if cell.name:
            marker += f" {cell.name}"
        if cell.at:
            marker += f' at="{cell.at}"'
        if cell.tags:
            marker += " tags=[" + ", ".join(f'"{tag}"' for tag in cell.tags) + "]"
        for key, raw in cell.extra:
            marker += f" {key}={raw}"
        chunks.append(f"{marker}\n{cell.code.rstrip(chr(10))}\n\n")
    return "".join(chunks).rstrip("\n") + "\n"


# --- reading and writing files ---------------------------------------------


def _warn(message: str) -> None:
    """Report a storage problem without derailing the debuggee (cf. config)."""
    print(f"judb: {message}", file=sys.stderr)


def load(path: Path) -> list[Cell]:
    """Read the cells in ``path``; a missing or unreadable file yields none.

    Parameters
    ----------
    path
        The percent-format file to read.

    Returns
    -------
    The cells it holds, or an empty list. Losing saved cells is bad; refusing to
    start the debugger over them would be worse, so every failure is a warning.
    """
    try:
        return parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        _warn(f"cannot read {path}: {exc}")
        return []


def save(path: Path, cells: list[Cell], *, header: str = "") -> None:
    """Write ``cells`` to ``path`` atomically.

    A temp file in the same directory plus :func:`os.replace`, so an interrupted
    save (or two saves racing) can never leave a half-written notebook where the
    user's cells used to be.

    Parameters
    ----------
    path
        The file to write.
    cells
        The cells to store.
    header
        Optional comment block for the top of the file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(dumps(cells, header=header), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _warn(f"cannot write {path}: {exc}")


# --- where the files are ---------------------------------------------------


def user_library_path() -> Path:
    """Return the user's own cell library (``$XDG_CONFIG_HOME/judb/cells.py``)."""
    return config.user_config_path().parent / "cells.py"


def project_library_path(root: Path | None = None) -> Path:
    """Return the project's committed cell library (``<root>/.judb/cells.py``).

    Parameters
    ----------
    root
        The project root; defaults to `judb.config.project_root`.
    """
    return (config.project_root() if root is None else root) / ".judb" / "cells.py"


def _slug(root: Path) -> str:
    """A readable, collision-free file stem for ``root``."""
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    name = re.sub(r"[^\w.-]", "_", root.name) or "root"
    return f"{name}-{digest}"


def notebook_path(root: Path | None = None) -> Path:
    """Return the auto-saved working notebook's path for a project.

    Under `judb.config.state_dir`, never inside the checkout: judb writes this
    file continuously, and the user's repository is written only when they
    explicitly ask (docs/PHASE3A_PLAN.md §6).

    Parameters
    ----------
    root
        The project root; defaults to `judb.config.project_root`.
    """
    root = config.project_root() if root is None else root
    return config.state_dir() / "notebooks" / f"{_slug(root)}.py"


def state_path(root: Path | None = None) -> Path:
    """Return the sidecar holding the watch list and active tab for a project."""
    return notebook_path(root).with_suffix(".toml")


def notebook_header(root: Path) -> str:
    """Return the comment block judb puts at the top of a saved notebook."""
    return (
        "# judb — saved debug cells for\n"
        f"#   {root}\n"
        "# Written automatically; percent cells, so this file opens as a notebook\n"
        "# in VS Code / Spyder / PyCharm and as plain Python everywhere else."
    )


# --- machine-local state (watches, active tab) -----------------------------


def load_state(path: Path) -> dict[str, Any]:
    """Read the TOML sidecar; a missing or broken file yields ``{}``."""
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _warn(f"cannot read {path}: {exc}")
        return {}


def save_state(path: Path, *, watches: list[str], active: str) -> None:
    """Write the watch list and active tab to ``path`` atomically.

    Strings are emitted with :func:`json.dumps`, whose escaping is a subset of
    TOML's basic strings — which matters, because a watch expression very often
    contains quotes (``df["value"]``).

    Parameters
    ----------
    path
        The sidecar to write.
    watches
        The watch expressions, in the browser's order.
    active
        The location key of the tab that was in front.
    """
    lines = [
        "# judb — machine-local UI state (watches, active tab).",
        f"active = {json.dumps(active)}",
        "watches = [" + ", ".join(json.dumps(expr) for expr in watches) + "]",
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _warn(f"cannot write {path}: {exc}")


# --- location keys ---------------------------------------------------------


def location_of(frame: FrameType, root: Path | None = None) -> str:
    """Return the location key for ``frame``.

    ``"<path relative to the project root>::<qualname>"`` — e.g.
    ``etl/clean.py::Cleaner.run``. A module-level frame keys to the file alone
    (a module frame's "function" *is* the file), and a file outside the project
    keeps its absolute path: only the machine-local tier can hold one anyway.

    Parameters
    ----------
    frame
        The frame to key — normally the *selected* one, which is already the
        subject of the console, the Variables pane and the watches.
    root
        The project root; defaults to `judb.config.project_root`.

    Returns
    -------
    The key, or ``""`` for a frame with no real file (``<string>``, ``<stdin>``)
    — nothing can be pinned to code that has no place on disk.
    """
    filename = frame.f_code.co_filename
    if not filename or filename.startswith("<"):
        return ""
    root = config.project_root() if root is None else root
    path = Path(filename)
    try:
        path = path.resolve()
    except OSError:  # pragma: no cover - a path the OS refuses to resolve
        pass
    try:
        where = path.relative_to(root).as_posix()
    except ValueError:
        where = path.as_posix()
    qualname = frame.f_code.co_qualname
    return where if qualname == "<module>" else f"{where}::{qualname}"


def matches(at: str, location: str) -> bool:
    """Whether a cell keyed ``at`` belongs at ``location``.

    The three rungs of one ladder: no key matches everywhere, a file key matches
    any function in that file, and a function key matches only itself.

    Parameters
    ----------
    at
        The cell's location key (possibly empty).
    location
        The selected frame's key, from `location_of`.

    Returns
    -------
    ``True`` if the cell should surface at that location.
    """
    if not at:
        return True
    if at == location:
        return True
    return "::" not in at and location.startswith(f"{at}::")


# --- the three tiers, as the browser sees them -----------------------------
#
# The wire carries a *flat* list of cells, exactly like the file: a cell's `at`
# is what groups it into a tab, so the browser does the grouping and the store
# keeps one obvious shape (docs/PHASE3A_PLAN.md §7).


def read_notebook(root: Path | None = None) -> dict[str, Any]:
    """Return the ``notebook`` message payload for a project.

    Parameters
    ----------
    root
        The project root; defaults to `judb.config.project_root`.

    Returns
    -------
    The saved cells, the location key of the tab that was in front, and the
    watch list — everything the browser needs to come back up where it left
    off, including after the *process* it was debugging has gone.
    """
    root = config.project_root() if root is None else root
    state = load_state(state_path(root))
    watches = state.get("watches")
    active = state.get("active")
    return {
        "cells": [cell.to_wire() for cell in load(notebook_path(root))],
        "active": active if isinstance(active, str) else "",
        "watches": [w for w in watches if isinstance(w, str)]
        if isinstance(watches, list)
        else [],
    }


def write_notebook(items: list[Cell], active: str, root: Path | None = None) -> None:
    """Save the working notebook (and which tab was in front).

    Parameters
    ----------
    items
        The cells, in the browser's order.
    active
        The location key of the active tab.
    root
        The project root; defaults to `judb.config.project_root`.
    """
    root = config.project_root() if root is None else root
    save(notebook_path(root), items, header=notebook_header(root))
    _update_state(root, active=active)


def write_watches(exprs: list[str], root: Path | None = None) -> None:
    """Persist the watch list next to the working notebook."""
    _update_state(config.project_root() if root is None else root, watches=exprs)


def _update_state(
    root: Path, *, watches: list[str] | None = None, active: str | None = None
) -> None:
    """Merge into the TOML sidecar, leaving the half we were not told alone."""
    path = state_path(root)
    current = load_state(path)
    if watches is None:
        stored = current.get("watches")
        watches = (
            [w for w in stored if isinstance(w, str)]
            if isinstance(stored, list)
            else []
        )
    if active is None:
        stored_active = current.get("active")
        active = stored_active if isinstance(stored_active, str) else ""
    save_state(path, watches=watches, active=active)


def read_library(root: Path | None = None) -> dict[str, Any]:
    """Return the ``library`` message payload: both libraries and their paths.

    Parameters
    ----------
    root
        The project root; defaults to `judb.config.project_root`.

    Returns
    -------
    The user's own cells and the project's committed ones, plus where each file
    lives, so the UI can tell the user which file a *save* is about to touch.
    """
    root = config.project_root() if root is None else root
    return {
        "user": [cell.to_wire() for cell in load(user_library_path())],
        "project": [cell.to_wire() for cell in load(project_library_path(root))],
        "paths": {
            "user": str(user_library_path()),
            "project": str(project_library_path(root)),
        },
    }


def write_library(scope: str, items: list[Cell], root: Path | None = None) -> None:
    """Replace one library file with ``items``.

    Writing the ``"project"`` scope is the *only* thing that puts a file inside
    the user's checkout, and it only ever happens because they asked
    (docs/PHASE3A_PLAN.md §6).

    Parameters
    ----------
    scope
        ``"user"`` or ``"project"``.
    items
        The whole library, in order — a save is a replace, so removing a cell
        works through the same path as adding one.
    root
        The project root; defaults to `judb.config.project_root`.
    """
    if scope == "user":
        save(
            user_library_path(),
            items,
            header=(
                "# judb — your own debug cells, available in every project.\n"
                f'# Cells tagged tags=["{SETUP_TAG}"] run themselves once per session.'
            ),
        )
        return
    save(
        project_library_path(config.project_root() if root is None else root),
        items,
        header=(
            "# judb — debug cells for this project, meant to be committed.\n"
            "# Anyone debugging here can load them; judb never runs them by itself."
        ),
    )
