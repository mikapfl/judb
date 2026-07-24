# Changelog

Notable user-visible changes to judb. Entries are collated from the fragments in
[`changelog.d/`](changelog.d/) by [towncrier](https://towncrier.readthedocs.io/)
at release time.

<!-- towncrier release notes start -->

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
