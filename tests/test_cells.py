"""Phase 3a — saved debug cells: the percent-format store and its location keys.

Three layers, cheapest first:

* **Format** — `judb.cells.parse` / `judb.cells.dumps` round-trips, including a
  file jupytext (not judb) wrote, since the whole point of the percent format is
  that the two can share a file.
* **Placement** — which file a tier resolves to, and the rule that judb
  auto-writes only *outside* the checkout.
* **Location keys** — `judb.cells.location_of` against real frames, and the
  three rungs of `judb.cells.matches`.

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


def test_load_of_a_missing_file_is_empty(tmp_path: Path):
    assert cells.load(tmp_path / "nope.py") == []


def test_state_sidecar_round_trips_quoted_expressions(tmp_path: Path):
    path = tmp_path / "state.toml"
    cells.save_state(path, watches=['df["value"]', "scale * 10"], active="a.py::f")
    assert cells.load_state(path) == {
        "watches": ['df["value"]', "scale * 10"],
        "active": "a.py::f",
    }


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
    assert cells.state_path(root) == notebook.with_suffix(".toml")


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


@pytest.mark.parametrize(
    ("at", "location", "expected"),
    [
        ("", "a/b.py::f", True),  # no key: always available
        ("a/b.py", "a/b.py::f", True),  # file key: anywhere in the file
        ("a/b.py", "a/b.py", True),  # …including its module frame
        ("a/b.py::f", "a/b.py::f", True),  # function key: only itself
        ("a/b.py::f", "a/b.py::g", False),
        ("a/b.py::f", "a/b.py", False),
        ("a/b.py", "a/bc.py::f", False),  # not a prefix match on the name
    ],
)
def test_the_location_ladder(at: str, location: str, expected: bool):
    assert cells.matches(at, location) is expected


# --- over the socket ------------------------------------------------------


def _ws_flow(url: str, send: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Connect, collect the pushed state, send ``send``, and return what arrived."""

    async def flow() -> dict[str, dict[str, Any]]:
        async with ClientSession() as session, session.ws_connect(ws_url(url)) as ws:
            got = {
                "notebook": await recv_type(ws, "notebook"),
                "library": await recv_type(ws, "library"),
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


def test_saving_a_library_writes_the_repo_and_echoes_it_back(tmp_path: Path):
    """The project tier is the one thing judb puts inside the checkout."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
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
        async with ClientSession() as session, session.ws_connect(ws_url(url)) as ws:
            await recv_type(ws, "library")
            await ws.send_json(
                {
                    "cmd": "save_cells",
                    "scope": "project",
                    "cells": [
                        {"code": "df.info()", "name": "shape", "at": "etl.py::run"}
                    ],
                }
            )
            library = await recv_type(ws, "library")
            await ws.send_json({"cmd": "continue"})
            await recv_type(ws, "running")
            return library

    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(root)
        config.reset()
        library = asyncio.run(flow())
    thread.join(timeout=5)
    config.reset()

    assert library["project"] == [
        {"code": "df.info()", "name": "shape", "at": "etl.py::run", "tags": []}
    ]
    written = (root / ".judb" / "cells.py").read_text()
    assert '# %% shape at="etl.py::run"' in written
    assert "df.info()" in written


DEBUGGEE = textwrap.dedent("""\
    import judb

    def clean(scale):
        judb.set_trace()
        return scale * 2

    clean(21)
    """)


def _run_debuggee(
    project: Path, env: dict[str, str], send: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Run the debuggee script once, drive one websocket session, and return
    the ``notebook`` message the server pushed on connect."""
    script = project / "demo.py"
    script.write_text(DEBUGGEE)
    proc = subprocess.Popen(
        [sys.executable, str(script)],
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
