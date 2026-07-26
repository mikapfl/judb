"""Phase 3 / B5 — process-level settings (``[tool.judb]`` / ``~/.config/judb``).

Three layers of coverage, from cheapest to most honest:

* **Resolution** — which file wins, and what a malformed one does. Driven
  through `judb.config.load_settings` with an explicit ``cwd``, so no test has
  to chdir the whole process (and so none of them can pick up judb's *own*
  ``pyproject.toml``).
* **Command line** — `judb.__main__._parse_options`, which turns judb's own
  leading flags into overrides while leaving the target's arguments alone.
* **End to end** — real ``python -m judb`` subprocesses proving the plan's exit
  criterion: ``[tool.judb] break_on_exception = false`` in a project is
  honored, and a CLI flag still beats it.
"""

import asyncio
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import ClientSession
from helpers import read_judb_url, recv_type, ws_url

from judb import __main__ as judb_main
from judb import config


@pytest.fixture(autouse=True)
def _isolated_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point the user-config layer at an empty temp dir and drop the cache.

    Settings are cached per process; without the reset a test that calls
    `judb.config.configure` would leak its overrides into every later test in
    the session.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config.reset()
    yield
    config.reset()


def _project(root: Path, body: str) -> Path:
    """Write a ``pyproject.toml`` containing ``body`` and return its directory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(body)
    return root


def _user_config(tmp_path: Path, body: str) -> None:
    """Write the per-user config file the `_isolated_settings` fixture points at."""
    path = config.user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_defaults_when_nothing_is_configured(tmp_path: Path):
    """No config anywhere: browser opens, crashes break, figures are inline PNGs."""
    # An empty pyproject.toml stops the upward walk here, so whatever sits above
    # the temp dir on the test machine can't reach into the result.
    project = _project(tmp_path / "proj", "")

    settings = config.load_settings(cwd=project)

    assert settings.open_browser is True
    assert settings.break_on_exception is True
    assert settings.figure_format == "png"


def test_user_config_file_is_read(tmp_path: Path):
    """``~/.config/judb/config.toml`` holds judb's keys at the top level."""
    _user_config(tmp_path, "open_browser = false\nfigure_format = 'interactive'\n")
    project = _project(tmp_path / "proj", "")

    settings = config.load_settings(cwd=project)

    assert settings.open_browser is False
    assert settings.figure_format == "interactive"
    assert settings.break_on_exception is True  # untouched by that file


def test_project_beats_user_config(tmp_path: Path):
    """A project's ``[tool.judb]`` overrides the user's file, key by key."""
    _user_config(tmp_path, "open_browser = false\nbreak_on_exception = false\n")
    project = _project(tmp_path / "proj", "[tool.judb]\nbreak_on_exception = true\n")

    settings = config.load_settings(cwd=project)

    assert settings.break_on_exception is True  # the project's opinion
    assert settings.open_browser is False  # nothing overrode the user's


def test_the_nearest_pyproject_wins_and_stops_the_walk(tmp_path: Path):
    """The first ``pyproject.toml`` going up *is* the project root.

    An inner checkout must not inherit an outer project's debugger settings, so
    the walk stops at the first file found — even when that file says nothing
    about judb.
    """
    _project(tmp_path / "outer", "[tool.judb]\nfigure_format = 'interactive'\n")
    inner = _project(tmp_path / "outer" / "inner", "[project]\nname = 'inner'\n")

    assert config.load_settings(cwd=inner).figure_format == "png"
    # ...and from the outer project itself, that setting does apply.
    assert config.load_settings(cwd=tmp_path / "outer").figure_format == "interactive"


def test_a_subdirectory_finds_the_project_above_it(tmp_path: Path):
    """Settings apply from anywhere inside the project, not just its root."""
    project = _project(tmp_path / "proj", "[tool.judb]\nopen_browser = false\n")
    deep = project / "src" / "pkg"
    deep.mkdir(parents=True)

    assert config.load_settings(cwd=deep).open_browser is False


@pytest.mark.parametrize(
    ("body", "expected_warning"),
    [
        ("[tool.judb]\nopen_browser = 'yes'\n", "expected true or false"),
        ("[tool.judb]\nfigure_format = 'svg'\n", "figure_format"),
        ("[tool.judb]\nopen_browsers = false\n", "unknown setting"),
        ("[tool.judb]\nthis is not toml\n", "not valid TOML"),
        ("[tool]\njudb = 'nope'\n", "not a table"),
    ],
)
def test_a_bad_setting_warns_and_falls_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, expected_warning: str
):
    """Config problems are warnings, never exceptions.

    Refusing to start the debugger over a stale key would be a poor trade: the
    debugger is the thing the user actually asked for.
    """
    project = _project(tmp_path / "proj", body)

    settings = config.load_settings(cwd=project)

    assert settings == config.Settings()  # every default intact
    assert expected_warning in capsys.readouterr().err


def test_configure_overrides_files_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`configure` layers explicit overrides on the files and caches the result."""
    project = _project(
        tmp_path / "proj",
        "[tool.judb]\nbreak_on_exception = false\nopen_browser = false\n",
    )
    monkeypatch.chdir(project)

    # `None` means "not specified on the command line": it must not clobber the
    # file's value with a default.
    settings = config.configure(break_on_exception=True, figure_format=None)

    assert settings.break_on_exception is True  # the override
    assert settings.open_browser is False  # still the file's
    assert settings.figure_format == "png"  # nobody had an opinion
    assert config.settings() is settings  # cached, so files are read once


def test_settings_are_resolved_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The cache is what keeps a debuggee's ``chdir`` from changing the answer."""
    project = _project(tmp_path / "proj", "[tool.judb]\nopen_browser = false\n")
    monkeypatch.chdir(project)
    assert config.settings().open_browser is False

    elsewhere = _project(tmp_path / "other", "")
    monkeypatch.chdir(elsewhere)

    assert config.settings().open_browser is False  # not re-read
    assert config.load_settings().open_browser is True  # ...but the files did change


# --- command line ---------------------------------------------------------


def test_options_stop_at_the_target():
    """judb's flags come first; everything from the target on is the target's.

    `python -m judb train.py --no-browser` must hand ``--no-browser`` to
    *train.py* — the alternative would make a target's flags unusable whenever
    they collide with judb's.
    """
    overrides, rest = judb_main._parse_options(
        ["--no-browser", "train.py", "--no-browser", "--epochs", "3"]
    )

    assert overrides == {"open_browser": False}
    assert rest == ["train.py", "--no-browser", "--epochs", "3"]


def test_double_dash_ends_judb_options():
    """``--`` hands the rest over, for a target whose name starts with a dash."""
    overrides, rest = judb_main._parse_options(["--no-browser", "--", "--odd.py"])

    assert overrides == {"open_browser": False}
    assert rest == ["--odd.py"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--figure-format", "interactive"], {"figure_format": "interactive"}),
        (["--figure-format=interactive"], {"figure_format": "interactive"}),
        (["--browser"], {"open_browser": True}),
        (["--break-on-exception"], {"break_on_exception": True}),
        (["--no-break-on-exception"], {"break_on_exception": False}),
    ],
)
def test_each_option_maps_to_its_setting(argv: list[str], expected: dict[str, Any]):
    overrides, rest = judb_main._parse_options([*argv, "script.py"])

    assert overrides == expected
    assert rest == ["script.py"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--nope", "script.py"],
        ["--figure-format", "svg", "script.py"],
        ["--figure-format"],
        ["--no-browser=1", "script.py"],
    ],
)
def test_a_bad_option_exits_two(argv: list[str], capsys: pytest.CaptureFixture[str]):
    """Usage errors fail loudly — unlike config files, a typed flag is intentional."""
    with pytest.raises(SystemExit) as exc:
        judb_main._parse_options(argv)

    assert exc.value.code == 2
    assert "judb: error:" in capsys.readouterr().err


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        judb_main._parse_options(["--help"])

    assert exc.value.code == 0
    assert "--figure-format" in capsys.readouterr().out


# --- end to end -----------------------------------------------------------


def _crashing_project(tmp_path: Path, pyproject: str) -> tuple[Path, Path]:
    """Lay out a project whose script crashes, and return (project dir, script)."""
    project = _project(tmp_path / "proj", pyproject)
    script = project / "crash_demo.py"
    script.write_text(
        textwrap.dedent(
            """
            def compute(rows):
                raise ValueError("bad rows")

            compute([1, 2, 3])
            """
        ).lstrip()
    )
    return project, script


def _run_to_completion(project: Path, script: Path, *flags: str) -> tuple[int, str]:
    """Run ``script`` under ``python -m judb`` from ``project``, resuming once.

    Continues past the stop-on-entry pause, then lets the process finish —
    however its crash policy says it should.

    Returns
    -------
    The exit code and the process's combined output.
    """
    env = {**os.environ, "JUDB_NO_BROWSER": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "judb", *flags, str(script)],
        cwd=project,  # so the project's pyproject.toml is the nearest one
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    lines: list[str] = []
    try:
        url = read_judb_url(proc, lines)

        async def flow() -> None:
            async with (
                ClientSession() as session,
                session.ws_connect(ws_url(url)) as ws,
            ):
                await recv_type(ws, "paused")
                await ws.send_json({"cmd": "continue"})
                await recv_type(ws, "running")

        asyncio.run(flow())
        return proc.wait(timeout=30), "".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_project_can_turn_off_break_on_exception(tmp_path: Path):
    """The B5 exit criterion: ``[tool.judb] break_on_exception = false`` is honored.

    The crash then behaves as it would have without judb — a traceback on the
    terminal and a non-zero exit — instead of parking the process in
    post-mortem waiting for a browser that an unattended run does not have.
    """
    project, script = _crashing_project(
        tmp_path, "[tool.judb]\nbreak_on_exception = false\n"
    )

    code, output = _run_to_completion(project, script)

    assert code == 1
    assert "ValueError: bad rows" in output
    # The traceback is the debuggee's own: judb's runner frames are trimmed.
    assert "bdb.py" not in output
    assert "judb/__main__.py" not in output


def test_a_flag_beats_the_project_config(tmp_path: Path):
    """``--break-on-exception`` re-enables post-mortem for one run.

    Same project, same script; only the command line differs — so the crash
    pauses instead of propagating, and the run does not end until the browser
    lets it.
    """
    project, script = _crashing_project(
        tmp_path, "[tool.judb]\nbreak_on_exception = false\n"
    )
    env = {**os.environ, "JUDB_NO_BROWSER": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "judb", "--break-on-exception", str(script)],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    lines: list[str] = []
    try:
        url = read_judb_url(proc, lines)

        async def flow() -> dict[str, Any]:
            async with (
                ClientSession() as session,
                session.ws_connect(ws_url(url)) as ws,
            ):
                await recv_type(ws, "paused")  # stop-on-entry
                await ws.send_json({"cmd": "continue"})
                await recv_type(ws, "running")
                crash = await recv_type(ws, "paused")  # post-mortem
                await ws.send_json({"cmd": "continue"})
                await recv_type(ws, "running")
                return crash

        crash = asyncio.run(flow())
        assert crash.get("postmortem") is True
        assert crash["exception"]["type"] == "ValueError"
        assert proc.wait(timeout=30) == 1
    finally:
        if proc.poll() is None:
            proc.kill()
