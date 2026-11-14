"""Phase 3a — saved debug cells: the percent-format store and its location keys.

Three layers, cheapest first:

* **Format** — `judb.cells.parse` / `judb.cells.dumps` round-trips, including a
  file jupytext (not judb) wrote, since the whole point of the percent format is
  that the two can share a file.
* **Placement** — which file a tier resolves to, and the rule that judb
  auto-writes only *outside* the checkout.
* **Libraries** — judb *edits* a library file the user also edits by hand, so
  adding, removing and re-keying must leave everything else in it alone.
* **Location keys** — `judb.cells.location_of` against real frames (which cells
  belong at a key is the browser's call; see ``connection.test.ts``).

The end-to-end claim ("kill the debuggee, start it again, the cells are there")
needs two real processes and lives in :mod:`test_entrypoints`-style subprocess
tests at the bottom of this file.
"""

import asyncio
import os
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path
from types import FrameType
from typing import Any

import pytest
from aiohttp import ClientSession
from helpers import read_judb_url, recv_type, ws_url

from judb import Debugger, cells, config


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point every XDG root at a temp dir and drop config's caches."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config.reset()
    yield
    config.reset()


# --- format ---------------------------------------------------------------


def test_parse_reads_name_location_and_tags():
    parsed = cells.parse(
        textwrap.dedent("""\
        # judb — a header comment, not a cell

        # %% describe the frame at="etl/clean.py::Cleaner.run"
        df.describe()

        # %% imports tags=["setup"]
        import numpy as np

        # %%
        1 + 1
        """)
    )
    assert [(c.name, c.at, c.tags) for c in parsed] == [
        ("describe the frame", "etl/clean.py::Cleaner.run", ()),
        ("imports", "", ("setup",)),
        ("", "", ()),
    ]
    assert parsed[0].code == "df.describe()"
    assert parsed[1].code == "import numpy as np"


def test_round_trip_preserves_everything():
    source = cells.dumps(
        [
            cells.Cell(code="df.head()", name="peek", at="a/b.py::f"),
            cells.Cell(code="import numpy as np", name="imports", tags=("setup",)),
            cells.Cell(code="x = 1\n\ny = 2", at="a/b.py"),
        ],
        header="# judb\n# a header",
    )
    assert cells.parse(source) == cells.parse(cells.dumps(cells.parse(source)))
    assert cells.parse(source)[2].code == "x = 1\n\ny = 2"


def test_unknown_metadata_survives_a_round_trip():
    """judb shares these files with jupytext; it must not eat its metadata."""
    source = '# %% a cell lines_to_next_cell=2 vscode={"languageId": "python"}\npass\n'
    [cell] = cells.parse(source)
    assert cell.name == "a cell"
    assert dict(cell.extra) == {
        "lines_to_next_cell": "2",
        "vscode": '{"languageId": "python"}',
    }
    assert "lines_to_next_cell=2" in cells.dumps([cell])
    assert 'vscode={"languageId": "python"}' in cells.dumps([cell])


def test_a_jupytext_file_loads():
    """A percent file judb never wrote: no header, a cell type in the name slot."""
    parsed = cells.parse(
        textwrap.dedent("""\
        import pandas as pd

        # %% [markdown]
        # not really code, but it is a cell

        # %% a plot
        df.plot()
        """)
    )
    assert [c.name for c in parsed] == ["", "", "a plot"]
    assert parsed[0].code == "import pandas as pd"


def test_empty_cells_are_dropped():
    assert cells.parse("# %% nothing here\n\n# %% real\n1\n") == [
        cells.Cell(code="1", name="real")
    ]


def test_marker_only_splits_at_column_zero():
    [cell] = cells.parse('# %% one\nprint("# %% not a marker")\n')
    assert cell.code == 'print("# %% not a marker")'


def test_save_is_atomic_and_leaves_no_debris(tmp_path: Path):
    path = tmp_path / "deep" / "cells.py"
    cells.save(path, [cells.Cell(code="1", name="one")], header="# judb")
    cells.save(path, [cells.Cell(code="2", name="two")])
    assert [p.name for p in path.parent.iterdir()] == ["cells.py"]
    assert [c.name for c in cells.load(path)] == ["two"]


def test_files_are_written_with_unix_line_endings(tmp_path: Path):
    """On every platform: a committed library must not flip between "\\n" and
    "\\r\\n" depending on who saved it last (Windows CI is what catches this)."""
    path = tmp_path / "cells.py"
    cells.save(path, [cells.Cell(code="1\n2", name="one")], header="# judb")
    assert b"\r" not in path.read_bytes()
    assert cells.load(path) == [cells.Cell(code="1\n2", name="one")]


def test_load_of_a_missing_file_is_empty(tmp_path: Path):
    assert cells.load(tmp_path / "nope.py") == []


def test_state_sidecar_round_trips_any_expression(tmp_path: Path):
    """Quotes, and text outside the BMP — a watch is arbitrary source."""
    path = tmp_path / "state.json"
    watches = ['df["value"]', "scale * 10", 'label == "😀"', "path\\to"]
    cells.save_state(path, watches=watches, active="a.py::f")
    assert cells.load_state(path) == {"watches": watches, "active": "a.py::f"}


def test_a_broken_sidecar_reads_as_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    path = tmp_path / "state.json"
    path.write_text("{ not json")
    assert cells.load_state(path) == {}
    assert "cannot read" in capsys.readouterr().err


# --- libraries: edited in place, never replaced ------------------------------

HAND_WRITTEN = textwrap.dedent("""\
    # a comment I wrote by hand at the top of my library

    # %% imports tags=["setup"]
    import math

    # %% everywhere snippet
    dir()

    # %% from another tool foo="bar"
    1 + 1
    """)


def _library(text: str = HAND_WRITTEN) -> Path:
    path = cells.user_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_adding_to_a_library_keeps_everything_already_in_it():
    """The bug this replaces: saving Scratch *replaced* every un-keyed cell —
    setup cells included — and rewrote the file from the browser's copy."""
    path = _library()
    cells.add_to_library("user", [cells.Cell(code="value + 1")])
    assert path.read_text() == HAND_WRITTEN + "\n# %%\nvalue + 1\n"


def test_adding_the_same_cell_twice_does_not_double_it():
    path = _library()
    cells.add_to_library("user", [cells.Cell(code="dir()"), cells.Cell(code="new")])
    cells.add_to_library("user", [cells.Cell(code="new")])
    assert [c.code for c in cells.load(path)] == [
        "import math",
        "dir()",
        "1 + 1",
        "new",
    ]


def test_a_cell_added_by_hand_mid_session_survives_a_removal():
    """The browser's copy of the library is from connect time; the file wins."""
    path = _library()
    stale = cells.load(path)  # what the browser was sent
    path.write_text(HAND_WRITTEN + "\n# %% added in my editor\nprint('hand edit')\n")
    cells.remove_from_library("user", stale[1])
    text = path.read_text()
    assert "everywhere snippet" not in text
    assert "print('hand edit')" in text
    assert text.startswith("# a comment I wrote by hand")
    assert '# %% from another tool foo="bar"' in text
    assert '# %% imports tags=["setup"]' in text


def test_rekeying_changes_only_the_key_of_the_named_cell():
    path = _library()
    other_tool = cells.load(path)[2]
    cells.rekey_in_library("user", other_tool, "etl/clean.py::run")
    assert path.read_text() == HAND_WRITTEN.replace(
        '# %% from another tool foo="bar"',
        '# %% from another tool at="etl/clean.py::run" foo="bar"',
    )


def test_an_edit_that_changes_nothing_does_not_touch_the_file():
    """Not even to tidy it: this is the user's file."""
    untidy = "#my header\n\n\n\n# %%   spaced   \n1\n"
    path = _library(untidy)
    cells.remove_from_library("user", cells.Cell(code="not in the file"))
    cells.add_to_library("user", [cells.Cell(code="1")])
    assert path.read_text() == untidy


def test_a_new_library_gets_judbs_own_header(tmp_path: Path):
    cells.add_to_library("project", [cells.Cell(code="1")], tmp_path)
    text = cells.project_library_path(tmp_path).read_text()
    assert text.startswith("# judb — debug cells for this project")
    assert cells.load(cells.project_library_path(tmp_path)) == [cells.Cell(code="1")]


def test_a_library_that_cannot_be_written_raises(tmp_path: Path):
    """An explicit save must not fail silently (the notebook's auto-save may)."""
    (tmp_path / ".judb").write_text("a file where the directory should be")
    with pytest.raises(OSError, match="judb"):
        cells.add_to_library("project", [cells.Cell(code="1")], tmp_path)


# --- placement ------------------------------------------------------------


def test_project_root_is_the_nearest_pyproject(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert config.project_root(cwd=root / "pkg" / "sub") == root.resolve()


def test_project_root_falls_back_to_the_starting_directory(tmp_path: Path):
    stray = tmp_path / "stray"
    stray.mkdir()
    assert config.project_root(cwd=stray) == stray.resolve()


def test_the_working_notebook_never_lands_in_the_checkout(tmp_path: Path):
    """judb auto-writes only outside the repo (PHASE3A_PLAN.md §6)."""
    root = tmp_path / "proj"
    root.mkdir()
    notebook = cells.notebook_path(root)
    assert root not in notebook.parents
    assert notebook.parent == config.state_dir() / "notebooks"
    assert notebook.stem.startswith("proj-")
    assert cells.state_path(root) == notebook.with_suffix(".json")


def test_two_checkouts_of_one_project_get_their_own_notebooks(tmp_path: Path):
    a, b = tmp_path / "a" / "proj", tmp_path / "b" / "proj"
    assert cells.notebook_path(a) != cells.notebook_path(b)


def test_the_project_library_is_the_only_thing_written_into_the_repo(tmp_path: Path):
    assert cells.project_library_path(tmp_path) == tmp_path / ".judb" / "cells.py"
    assert cells.user_library_path() == config.user_config_path().parent / "cells.py"


# --- location keys --------------------------------------------------------


def _frame_in(root: Path, source: str, name: str = "mod.py") -> FrameType:
    """Execute ``source`` (which must call ``capture()``) and return that frame."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    captured: list[FrameType] = []

    def capture() -> None:
        import sys

        frame = sys._getframe(1)
        captured.append(frame)

    namespace: dict[str, object] = {"capture": capture}
    # Compiling with the file's real name is the point: `location_of` reads
    # `co_filename`, so the frame has to believe it came from there.
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102
    return captured[0]


def test_location_of_a_method_carries_the_class(tmp_path: Path):
    frame = _frame_in(
        tmp_path,
        "class Cleaner:\n    def run(self):\n        capture()\nCleaner().run()\n",
        name="etl/clean.py",
    )
    assert cells.location_of(frame, tmp_path) == "etl/clean.py::Cleaner.run"


def test_location_of_a_module_frame_is_the_file(tmp_path: Path):
    frame = _frame_in(tmp_path, "capture()\n")
    assert cells.location_of(frame, tmp_path) == "mod.py"


def test_location_outside_the_project_keeps_its_absolute_path(tmp_path: Path):
    frame = _frame_in(tmp_path / "elsewhere", "def f():\n    capture()\nf()\n")
    key = cells.location_of(frame, tmp_path / "proj")
    assert key.startswith("/") and key.endswith("mod.py::f")


def test_a_frame_with_no_file_has_no_location():
    exec_frame: list[FrameType] = []

    def capture() -> None:
        import sys

        exec_frame.append(sys._getframe(1))

    exec("capture()", {"capture": capture})  # noqa: S102 — a frame with no file
    assert cells.location_of(exec_frame[0], Path("/tmp")) == ""


# --- over the socket ------------------------------------------------------


def _ws_flow(url: str, send: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Connect, collect the pushed state, send ``send``, and return what arrived."""

    async def flow() -> dict[str, dict[str, Any]]:
        async with ClientSession() as session, session.ws_connect(ws_url(url)) as ws:
            got = {
                "notebook": await recv_type(ws, "notebook"),
                "library": await recv_type(ws, "library"),
                "paused": await recv_type(ws, "paused"),
            }
            for message in send:
                await ws.send_json(message)
            # Storage is answered by the server thread; a round-trip through the
            # *debuggee* thread proves everything queued before it has landed.
            await ws.send_json({"cmd": "complete", "code": "", "cursor": 0})
            await recv_type(ws, "completions")
            await ws.send_json({"cmd": "continue"})
            await recv_type(ws, "running")
            return got

    return asyncio.run(flow())


def _library_reply(root: Path, command: dict[str, Any]) -> dict[str, Any]:
    """Pause an in-process debuggee in project ``root``, send one library
    command, and return the ``library`` message that answers it."""
    with pytest.MonkeyPatch.context() as patch:
        # Before the debuggee thread exists: it resolves the project root at
        # its first pause, and must find this one.
        patch.chdir(root)
        config.reset()

        dbg = Debugger()
        url = dbg.start_server(open_browser=False)

        def debuggee() -> None:
            scale = 3
            dbg.set_trace()
            assert scale == 3

        thread = threading.Thread(target=debuggee)
        thread.start()

        async def flow() -> dict[str, Any]:
            async with (
                ClientSession() as session,
                session.ws_connect(ws_url(url)) as ws,
            ):
                await recv_type(ws, "library")
                await ws.send_json(command)
                library = await recv_type(ws, "library")
                await ws.send_json({"cmd": "continue"})
                await recv_type(ws, "running")
                return library

        library = asyncio.run(flow())
        thread.join(timeout=5)
    config.reset()
    return library


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    return root


def test_saving_to_the_project_library_writes_the_repo_and_echoes_it_back(
    tmp_path: Path,
):
    """The project tier is the one thing judb puts inside the checkout."""
    root = _project(tmp_path)
    library = _library_reply(
        root,
        {
            "cmd": "library_add",
            "scope": "project",
            "cells": [{"code": "df.info()", "name": "shape", "at": "etl.py::run"}],
        },
    )
    assert library["project"] == [
        {"code": "df.info()", "name": "shape", "at": "etl.py::run", "tags": []}
    ]
    assert "error" not in library
    written = (root / ".judb" / "cells.py").read_text()
    assert '# %% shape at="etl.py::run"' in written
    assert "df.info()" in written


def test_a_failed_library_save_is_reported_to_the_browser(tmp_path: Path):
    root = _project(tmp_path)
    (root / ".judb").write_text("a file where the directory should be")
    library = _library_reply(
        root,
        {"cmd": "library_add", "scope": "project", "cells": [{"code": "1"}]},
    )
    assert "could not save the cell library" in library["error"]
    assert library["project"] == []


DEBUGGEE = textwrap.dedent("""\
    import judb

    def clean(scale):
        judb.set_trace()
        return scale * 2

    clean(21)
    """)

# The same program, except that it changes directory before it first pauses —
# what `monkeypatch.chdir(tmp_path)` or a script moving to its data does.
WANDERING_DEBUGGEE = textwrap.dedent("""\
    import os
    import sys

    import judb

    def clean(scale):
        os.chdir(sys.argv[1])
        judb.set_trace()
        return scale * 2

    clean(21)
    """)


def _run_debuggee(
    project: Path,
    env: dict[str, str],
    send: list[dict[str, Any]],
    *,
    source: str = DEBUGGEE,
    args: tuple[str, ...] = (),
) -> dict[str, dict[str, Any]]:
    """Run the debuggee script once, drive one websocket session, and return
    what the server pushed on connect (``paused``, ``notebook``, ``library``)."""
    script = project / "demo.py"
    script.write_text(source)
    proc = subprocess.Popen(
        [sys.executable, str(script), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=project,
        bufsize=1,
    )
    lines: list[str] = []
    try:
        url = read_judb_url(proc, lines)
        got = _ws_flow(url, send)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return got


def test_the_notebook_outlives_the_process(tmp_path: Path):
    """The exit criterion: run a cell, kill the debuggee, start it again — the
    cells are there. Two *real* processes, because a reconnect would prove only
    that the server remembers, which it did before and which is not the bug."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    env = {
        **os.environ,
        "JUDB_NO_BROWSER": "1",
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }

    first = _run_debuggee(
        project,
        env,
        [
            {
                "cmd": "save_notebook",
                "active": "demo.py::clean",
                "cells": [
                    {"code": "scale * 2", "name": "", "at": "", "tags": []},
                    {"code": "df.plot()", "name": "the plot", "at": "demo.py::clean"},
                ],
            },
            {"cmd": "set_watches", "exprs": ['scale, "x"']},
        ],
    )
    assert first["notebook"]["cells"] == []  # nothing saved yet on the first run

    second = _run_debuggee(project, env, [])
    assert second["notebook"]["cells"] == [
        {"code": "scale * 2", "name": "", "at": "", "tags": []},
        {"code": "df.plot()", "name": "the plot", "at": "demo.py::clean", "tags": []},
    ]
    assert second["notebook"]["active"] == "demo.py::clean"
    assert second["notebook"]["watches"] == ['scale, "x"']

    # And it landed outside the checkout, as readable percent cells.
    assert not (project / ".judb").exists()
    saved = next((tmp_path / "state" / "judb" / "notebooks").glob("proj-*.py"))
    assert '# %% the plot at="demo.py::clean"' in saved.read_text()


def test_a_debuggee_that_changes_directory_is_still_the_same_project(tmp_path: Path):
    """The project is where the program was *started*, not where it happens to
    be standing when it first pauses — otherwise the notebook is filed under a
    directory nobody will ever debug from again, and never comes back."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {
        **os.environ,
        "JUDB_NO_BROWSER": "1",
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    save = {
        "cmd": "save_notebook",
        "active": "",
        "cells": [{"code": "scale * 2", "name": "", "at": "", "tags": []}],
    }

    got = _run_debuggee(
        project, env, [save], source=WANDERING_DEBUGGEE, args=(str(elsewhere),)
    )
    # Keyed relative to the project, and filed under it.
    assert got["paused"]["location"] == "demo.py::clean"
    assert got["library"]["paths"]["project"] == str(project / ".judb" / "cells.py")
    [saved] = (tmp_path / "state" / "judb" / "notebooks").glob("*.py")
    assert saved.stem.startswith("proj-")

    # …so the next, well-behaved run of the same project finds the cells.
    again = _run_debuggee(project, env, [])
    assert [c["code"] for c in again["notebook"]["cells"]] == ["scale * 2"]
