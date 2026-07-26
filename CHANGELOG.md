# Changelog

Notable user-visible changes to judb. Entries are collated from the fragments in
[`changelog.d/`](changelog.d/) by [towncrier](https://towncrier.readthedocs.io/)
at release time.

<!-- towncrier release notes start -->

## [0.3.0](https://github.com/mikapfl/judb/releases/tag/v0.3.0) — 2026-07-26

### Added

- A **Breakpoints pane** (bottom row, left of the call stack) lists every
  breakpoint across all files with its condition, temporary and ignore-count
  options, grouped by file. Remove any breakpoint from the pane with its ×
  button — including one in a file you are not currently viewing. ([#4](https://github.com/mikapfl/judb/issues/4))
- Breakpoints can now carry a **condition**, a **temporary** flag (clears itself
  after firing once), and an **ignore count** (skip the first *n* hits).
  Right-click a line in the source gutter to open the breakpoint editor; a
  conditional breakpoint shows as a diamond marker with the condition in its
  tooltip. Clicking a blank or comment-only line now sets the breakpoint on the
  next line that has code (which would otherwise never trigger), or shows a
  dismissable notice when there is none. ([#4](https://github.com/mikapfl/judb/issues/4))
- **Break on exceptions.** `python -m judb script.py` now catches an uncaught
  crash and drops you into post-mortem on the failing frame — inspect the crash's
  locals in the console instead of losing the process to a terminal traceback (the
  process still exits non-zero). A new **Exception** pane (bottom-right) shows the
  crash whenever a pause is post-mortem (this also covers `pytest --pdb
  --pdbcls=judb:Debugger`); it stays empty on an ordinary pause. It lays the
  traceback out the way Python does — one `file.py:line in function` block per
  frame, with a window of source around the failing line, that line marked, and
  `^^^^` under the sub-expression that actually failed — and chained exceptions
  (`raise ... from ...`) each get their own section. The source is
  syntax-highlighted **with the editor's own theme**, so the traceback, the Source
  pane, and console cells colour Python identically and follow a theme switch
  together. Clicking a frame header in the traceback selects that frame — source,
  variables, and the console retarget to it, exactly as clicking it in the call
  stack would. ([#5](https://github.com/mikapfl/judb/issues/5))
- **Watch expressions.** A new **Watch** pane (under Variables) keeps a list of
  expressions you care about — `df.shape`, `total / n`, `arr.mean()` — and
  re-evaluates them in the selected frame on every pause, frame change and console
  cell run, so you can watch a value change as you step. Unfold a row to see the
  full value the way the console renders it (a watched DataFrame shows its HTML
  table). An expression that doesn't resolve in the current frame reports its error
  on its own row, leaving the rest of the pane intact, and the list is remembered
  across a refresh and the next run of the same program. ([#6](https://github.com/mikapfl/judb/issues/6))
- **Open any file, and break in code you haven't reached yet.** The Source pane
  can now show a file no frame is in: type a path into *Open file…* (or pick one
  of the files judb already knows), set a breakpoint in the gutter as usual, and
  `continue` — the debuggee stops there the first time it runs that line. Rows in
  the Breakpoints pane and frames in the Exception pane that have already unwound
  navigate to their file the same way. While you are browsing, the pane says so
  plainly and offers a *Back to frame* button, so an unhighlighted file can never
  be mistaken for the paused one. ([#7](https://github.com/mikapfl/judb/issues/7))
- **Settings.** judb still needs no configuration, but now takes some when you
  want it: `[tool.judb]` in your project's `pyproject.toml` (or
  `~/.config/judb/config.toml` for every project) sets whether a browser tab opens
  (`open_browser`), whether `python -m judb` pauses on the target's first line
  (`stop_on_entry`), whether it lands in post-mortem on an uncaught crash or lets
  it propagate (`break_on_exception`), and whether figures come back as inline
  PNGs or as live interactive canvases (`figure_format`, so `%matplotlib judb`
  need not be typed every session).

  `stop_on_entry = false` is the "start it and walk away" mode: the program runs
  at full speed and judb only takes over on a crash — or wherever your code calls
  `breakpoint()`.

  `python -m judb` grew the matching flags — `--no-browser`,
  `--no-stop-on-entry`, `--no-break-on-exception`, `--figure-format` — which
  override the files for one run. They go before the script, so the script keeps
  its own flags: `python -m judb --no-stop-on-entry train.py --epochs 3`. See
  `--help`, or the README's *Configuration* section. ([#8](https://github.com/mikapfl/judb/issues/8))

### Changed

- Terminal-coloured output (a debuggee's coloured `print`, `obj?` introspection,
  the console's own traceback) now paints from the theme's 16 ANSI colours instead
  of fixed ones, so it reads correctly in both light and dark mode. ([#5](https://github.com/mikapfl/judb/issues/5))

### Fixed

- Setting a breakpoint on an empty / comment only line now sets the breakpoint on the next line with a statement instead. ([#4](https://github.com/mikapfl/judb/issues/4))
- Output from the debuggee is now HTML-escaped before it reaches the browser.
  Previously a program that printed markup (or a repr containing it) had that
  markup rendered as part of judb's own page instead of shown as text. ([#5](https://github.com/mikapfl/judb/issues/5))
- Refreshing the browser tab after selecting a stack frame now comes back on
  *that* frame. Previously the reloaded tab showed the innermost frame while the
  console, variables and watches were still answering for the one you had
  selected. ([#6](https://github.com/mikapfl/judb/issues/6))


## [0.2.0](https://github.com/mikapfl/judb/releases/tag/v0.2.0) — 2026-07-24

### Fixed

- Installing judb from a source checkout (`uv sync`, `pip install .`) no longer
  hangs when your corepack-managed pnpm differs from the pinned version: the
  frontend build now fetches the pinned pnpm automatically instead of waiting on a
  confirmation prompt that never reaches you. ([#3](https://github.com/mikapfl/judb/issues/3))

### Misc

- [#2](https://github.com/mikapfl/judb/issues/2)


## [0.1.0](https://github.com/mikapfl/judb/releases/tag/v0.1.0) — 2026-07-24

### Added

- First public release. judb is a browser-based visual debugger for scientific
  Python: pudb-style stepping alongside a notebook-style console whose cells run
  **in the currently-paused stack frame**, so you can plot and inspect a paused
  program's real DataFrames, arrays and figures the way you would in a notebook.
- `%matplotlib judb` renders zoomable, pannable matplotlib figures in the browser
  while the debuggee is paused — matplotlib's own WebAgg engine, no Jupyter kernel
  required.
- `pytest --pdb --pdbcls=judb:Debugger` drops you into the browser UI paused at a
  failing test, with the console live in the frame that blew up. `--trace` (break
  at the start of each test) and `breakpoint()` inside a test work too.
- `python -m judb your_script.py [args]` and `python -m judb -m your.module [args]`
  run a script or module under the debugger without editing it, stopping on the
  first line.

### Changed

- `pip install judb` no longer pulls in numpy, pandas or pre-commit. judb renders
  whatever a cell produces and never imported them itself.

### Fixed

- A process forked after the parent already started a debugger session now gets
  its own UI and URL, instead of pausing silently with no way to reach it.
- Refreshing the browser tab while paused now restores the debugger UI instead of
  leaving a blank page on a debuggee that is still waiting. A dropped connection
  (laptop sleep, a network blip) also reconnects on its own.
- `judb.set_trace()` on the last line of a script now pauses in your own code.
  Previously tracing outlived the program and the browser showed interpreter
  shutdown internals.

### Documentation

- Documented what works when the debuggee is not on the main thread — including
  that a terminal Ctrl+C will not end a program paused in a worker thread, and
  that two threads pausing at once is not supported yet.

### Misc

- [#1](https://github.com/mikapfl/judb/issues/1)
