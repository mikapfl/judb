# Phase 3 — Detailed Plan ("Fit & finish", shipping first)

*Companion to `IMPLEMENTATION_PLAN.md` §5. That file's Phase 3 bullet is a
grab-bag; this doc turns it into an ordered, concrete plan grounded in the
Phase-2a codebase. Same conventions: **[DECISION]** = recommended but open,
**[OPEN]** = needs your steer, each work item lists its **touchpoints** and an
**exit** check.*

## 0. Shape: two waves, ship first

> **Status: Wave A shipped — `judb 0.1.0` is on PyPI** (`0.2.0` followed with
> install/CI tweaks). `pip install judb` works and the entry points
> (`python -m judb`, `pytest --pdbcls`, `set_trace`) all land in the browser UI.
>
> **Wave B in progress.**
> - **B1 ✅ done and merged** (PR #4, *feat: better breakpoints*), which also
>   delivered B4's breakpoints panel.
> - **B2 ✅ done** (uncaught path, branch `break-on-exception`) — `-m judb`
>   catches a crash into post-mortem and an Exception pane surfaces it. The
>   "break on all raised" toggle is deferred (see B2). The pane was then
>   **reworked to render structured tracebacks** rather than IPython's ANSI, so
>   all code in the UI is themed from one palette — that rework set a new
>   invariant (see B2's *[DECISION] structure, never colour*, and `CLAUDE.md`
>   "Three invariants"). Traceback frames are clickable, which is the first half
>   of B4's navigation story.
> - **B3 ✅ done** (branch `watch-expressions`) — a Watch pane of pinned
>   expressions, re-evaluated in the selected frame on every pause / frame
>   change / cell run, with the list owned and persisted by the browser.
> - **B4 ✅ done** (branch `open-file`) — `open_file` + a browse mode in the
>   Source pane, so a breakpoint can be set in a file the debuggee has not
>   reached yet; the Breakpoints and Exception panes navigate to it.
> - **B5 ✅ done** (branch `settings`) — `judb/config.py`: `[tool.judb]` /
>   `~/.config/judb/config.toml` for the three process-level options, plus the
>   first real `python -m judb` flags. A *user theme* is the obvious next
>   setting and stays open (see B5).
>
> **Wave B is complete.** What follows is Phase 3a (saved/loadable debug cells)
> — see `IMPLEMENTATION_PLAN.md` §5.

Phase 2a gave us "something to show the world" — but only to someone sitting at a
checkout running `make frontend` first. **Wave A makes it *reachable*** (a real
`pip install`, the entry points people actually use, docs). **Wave B makes it
*deeper*** (conditional breakpoints, break-on-exception, watch, multi-file). The
ordering is the priority: nothing in Wave B matters if a new user can't get to a
paused frame from their own workflow.

Nothing here should need to break the mime-bundle contract or the threading model
(`CLAUDE.md` "Two invariants"). Wave A is mostly Python + packaging + docs; Wave B
adds a handful of protocol messages and small frontend panes. The seam stays the
queues.

```
Wave A — SHIP           Wave B — DEEPEN
  A1 install story        B1 conditional / temporary / ignore breakpoints
  A2 entry points         B2 break-on-exception + post-mortem
  A3 real-program         B3 watch expressions
     robustness           B4 multi-file source navigation
  A4 docs + PyPI          B5 settings / config
```

---

## Wave A — Ship

### A1. Install story & packaging hygiene

The wheel/sdist plumbing (hatch build hook, gitignored bundle, sdist ships the
prebuilt bundle) already exists and is sound. The gaps are dependency hygiene and
proof that a clean install works.

- **Bug: `pre-commit` is a runtime dependency** (`pyproject.toml:16`). A
  `pip install judb` currently pulls pre-commit (a dev-only git-hook tool) into
  the user's env. Move it to the `dev` dependency-group. **This is the first fix.**
- **[DECISION] Audit and slim runtime deps.** Today's runtime set is `aiohttp`,
  `ipython`, `matplotlib`, `matplotlib-inline`, `numpy`, `pandas`. `numpy`/`pandas`
  are heavy and only truly needed by demos/tests, not by judb's core — judb renders
  *whatever* mime bundles a cell emits and never imports pandas itself. Recommend:
  drop `numpy`/`pandas` from core runtime deps (keep them in the `example`/dev
  extras). Keep `matplotlib`/`matplotlib-inline` in core (the inline backend and
  `%matplotlib judb` are headline features). Net: a much lighter core install.
- **Licensing.** Use `pylic` to make sure our licensing is sound.
- **Wheel/sdist smoke test.** A CI/`make` target that builds the wheel *and* the
  sdist in a clean venv (no Node) and asserts `import judb; judb.set_trace` works
  and `judb/static/index.html` is present. The sdist path is the fragile one (it
  reuses the shipped bundle instead of rebuilding) — it needs an explicit test.
  *Exit:* `pip install dist/judb-*.whl` in a fresh venv, then a scripted headless
  `set_trace(open_browser=False)` round-trip (reuse `tests/test_server.py`'s ws
  harness) passes.
- **Python floor. ✅ Resolved.** Held at 3.13 (open decision #1); CI runs the
  suite on **3.13 and 3.14** (`.github/workflows/ci.yml`). Verified green on 3.14
  before wiring the matrix, so the forward guard starts honest.
- **CI. ✅ Done.** `ci.yml` on every PR and push to `main`: tests (3.13/3.14),
  lint + `pylic`, frontend (svelte-check/Vitest/Playwright), and the wheel+sdist
  install smoke.

### A2. Entry points — meet users where they are

`import judb; judb.set_trace()` and `PYTHONBREAKPOINT=judb.set_trace` work today.
The two missing ones from §3/§4 are what make judb usable without editing code.

- **`python -m judb script.py [args]`** — new `judb/__main__.py` (currently absent
  despite being in the §4 layout). Runs the target script under the debugger from
  line one (or `-c`/`-m` forms, à la `pdb`). Model it on `pdb.main()`: set
  `sys.argv`, exec the script's code in a fresh `__main__` namespace under
  `Debugger.run()`. Decide default-stop behavior: **[DECISION]** stop on the first
  line (like `pdb`) vs. run-until-first-`breakpoint()`/exception (friendlier for
  "just run it and catch the crash"). Recommend the latter as the default, with a
  `--start` flag for stop-on-entry.
- **`pytest --pdbcls=judb:Debugger`** — the near-free, highest-value entry point
  (open decision #4 = "yes, pytest-first"). *This needs a spike*: pytest
  instantiates the pdb class and may pass constructor kwargs (e.g. `stdout`) our
  `Debugger.__init__(skip=...)` doesn't accept, and drives it via `set_trace` **and**
  post-mortem (`reset()` + `interaction(frame, traceback)`). Our `interaction()`
  signature takes only a frame and never handled a traceback. Tasks: (a) make
  `__init__` tolerate pytest's kwargs; (b) support the post-mortem entry (ties into
  **B2**); (c) an integration test that runs pytest on a failing test with
  `--pdbcls=judb:Debugger` and asserts a `paused` at the failure frame.
- **Harden `set_trace` for repeated/last-line use.** Confirm multiple
  `set_trace()` calls in one run reuse the one server/browser tab (the
  `_active_debugger` singleton suggests yes — add a test), and that pausing on the
  *last* line of a program doesn't strand the browser.

### A3. Real-program robustness

Things that don't show up in the demo scripts but bite real users.

- **Program-end UX.** `_notify_finished` emits `finished` then `sleep(0.3)`. Verify
  the tab shows a clear terminal state (it does show "debuggee exits" per commit
  `17532b0`) and that Ctrl+C in the terminal while paused still ends the program
  (the SIGINT re-assert dance in `debugger.py` handles the polars case — add a
  regression note/test).
- **Worker-thread & multiprocess debuggees — scope the promise. ✅ Done.**
  Behaviour established by experiment and written up in the README's *Threads and
  processes* section. Verified: a single worker thread pauses and runs cells
  normally, but interrupt degrades to `SetAsyncExc` (cannot break a blocking
  call) and a terminal Ctrl+C does **not** end the program (the
  `KeyboardInterrupt` goes to the main thread while the worker keeps waiting).
  Two threads pausing concurrently does not crash but shows only the latest
  pause and releases an arbitrary one per Continue — **[RESOLVED]** declared
  unsupported for now rather than serialized; real multi-thread debugging is
  Phase 4. Child processes each get their own server/tab.
  *Fixed along the way:* a `fork()`ed child inherited the parent's `DebugServer`
  object without the threads running it, so `start_server` short-circuited and
  the child paused with no UI and no URL — a silent hang. `start_server` now
  reuses a server only when its recorded pid matches.
- **Port/token & reconnect.** Confirm a browser refresh reconnects cleanly
  (outbound buffering in `server.py` covers pre-connect; verify mid-session
  reconnect doesn't drop the current `paused` state — may need the server to
  re-emit last state on connect).

### A4. Docs & release

- **README quickstart is already good** — add: an animated demo GIF (the headline
  "plot a paused DataFrame" loop), a one-line pytest example, and a short "how it
  works / why not just pdb" paragraph pointing at `REQUIREMENT_ANALYSIS.md`.
- **CHANGELOG + version. ✅ Done.** Changelog managed with **towncrier**:
  fragments in `changelog.d/`, collated by `make changelog` at release time, so
  branches in flight never conflict over `CHANGELOG.md`; CI runs
  `towncrier check` on PRs. Version single-sourced as **`[project] version` in
  `pyproject.toml`** — static, because `uv version --bump` (which the release
  workflow runs) refuses a dynamic version; `judb.__version__` reports the
  *installed* distribution via `importlib.metadata`, so nothing is written down
  twice. `0.1.0` fragments for the Wave-A work are already written.
- **Publish to PyPI as `0.1.0`. ✅ Done.** `judb 0.1.0` is live on PyPI —
  `pip install judb` works. This is the concrete "show the world" deliverable and
  the Wave A exit. The one-time setup (trusted publishers + `testpypi`/`pypi`
  GitHub environments) and the Test-PyPI dry run are all complete;
  `.github/workflows/release.yml` (`workflow_dispatch`-only, `testpypi`/`pypi`
  target, rebuilds + re-verifies tests and the install smoke before publishing via
  Trusted Publishing) drove the release. `RELEASING.md` documents the procedure for
  the next bump.

**Wave A exit: ✅ reached.** A stranger runs `pip install judb`, then either
`python -m judb their_script.py` or `pytest --pdbcls=judb:Debugger` (or adds
`judb.set_trace()`), and lands in the browser UI — with no checkout, no Node, no
`make frontend`. (Remaining polish — the README demo GIF — is nice-to-have, not a
gate.)

---

## Wave B — Deepen the debugger

Each item is a small protocol addition + a pane/affordance. Keep `src/protocol.ts`
in sync with `judb/protocol.py` (the hand-mirrored contract).

### B1. Conditional / temporary / ignore-count breakpoints — ✅ Done (PR #4)

Nearly free — `bdb.set_break(filename, lineno, temporary, cond, funcname)` already
supports all of it; `_toggle_break` just hard-coded none of the options through.

- **Backend.** The `set_break` command carries optional `cond` (string),
  `temporary` (bool) and `ignore` (int); a re-set on a line *replaces* its
  breakpoint rather than stacking a second bdb `Breakpoint` on it (so the options
  act as an update). `do_clear` had to be implemented: bdb calls it to auto-remove
  a temporary breakpoint after it fires, and base `Bdb` leaves it abstract — a
  one-shot breakpoint would otherwise `NotImplementedError`. The `breakpoints`
  reply and every frame view now carry rich `{line, cond, temporary, ignore}`
  records (mirrored as `Breakpoint`/`BreakpointLocation` in `protocol.ts`).
- **Frontend.** A conditional/one-shot breakpoint renders as a diamond marker
  (warn colour) with its options in a tooltip. Right-clicking the gutter opens a
  popover editor (condition / temporary / ignore) — triggered off the
  `contextmenu` event so `preventDefault` reliably cancels the native menu
  (a `mousedown`-based first cut leaked the browser menu in Firefox once the
  popover backdrop became the event target).
- **Two follow-ons landed with it.** (a) Setting a breakpoint on a blank or
  comment-only line now **snaps forward** to the next line with executable code —
  found by compiling the source and collecting each code object's `co_lines()` —
  or raises a **dismissable notice** bar (new `notice` store field) when there is
  none. (b) Breakpoints **survive a browser reconnect**: the server folds each
  one-shot `breakpoints` message into the cached paused snapshot, and file
  identity is bdb's *canonical* path throughout the protocol (`_frame_view` /
  `_stack_summary` too), so clearing a breakpoint from the pane also clears its
  gutter dot on Windows (raw `co_filename` vs `normcase`d canonic had diverged).
- *Exit met:* `cond="i == 3"` fires only on that iteration and a temporary bp
  auto-clears after firing — both ws-tested; plus snap/reconnect ws tests, store
  vitest, and Playwright (popover, pane, refresh).

### B2. Break-on-exception & post-mortem — ✅ Done (uncaught path; PR #5)

This is what makes the **pytest-failure** and `-m judb` "catch the crash"
workflows real. The post-mortem machinery already existed (pytest's `--pdb`
enters `interaction(None, exc)`); this wave wired the same landing into
`-m judb` and gave the crash a home in the UI.

- **Backend.** `python -m judb script.py` now wraps the run: an uncaught
  exception (anything but `SystemExit`/`KeyboardInterrupt`) is handed to a new
  `Debugger.post_mortem(exc)`, which `reset()`s and enters `interaction(None,
  exc)` — the browser lands on the failing frame with the crash's real locals
  live in the console, instead of the process dying with only a terminal
  traceback. judb's own runner frames (`_run_code` / `bdb.run`'s `exec`) are
  trimmed off the top of the traceback (walk to the frame whose code *is* the
  target), so the post-mortem stack starts at the debuggee's module frame. The
  process still exits non-zero (`SystemExit(1)`) so the failure signal survives.
  The `paused` message's `exception` carries a structured `chain` (see below)
  alongside `type`/`message`.
- **Frontend.** A new **Exception** pane (rightmost on the bottom row) shows the
  exception type, message, and traceback whenever a pause is post-mortem, and
  stays empty otherwise. The store tracks `exception`/`postmortem` (set on
  `paused`, cleared on resume). `ExceptionInfo` mirrored into `protocol.ts`.
  A traceback frame that is still on the stack is a button that calls
  `selectFrame` — the pane matches it to a `stack` entry on
  filename+lineno+function rather than trusting index alignment, which is why
  `format_traceback` takes `Bdb.canonic` (both sides must spell the file the
  same way). Frames from a chained *cause* have unwound, match nothing, and stay
  inert.
- **[DECISION] the backend ships traceback *structure*, never colour.**
  `judb/tracebacks.py` turns an exception into a JSON chain (oldest cause first;
  per frame: file, line, function, a source window, and 3.11+ anchor columns)
  using stdlib `traceback` only. The first cut instead shipped IPython's
  ANSI-coloured traceback and mapped the escape codes to CSS in the browser —
  which meant reverse-engineering *which Pygments token* an ANSI colour stood
  for (`.ansi-palette-28-fg → --tok-keyword`), a mapping that is a guess, is
  incomplete by construction, and rots whenever IPython changes style. Worse, it
  aliased the ANSI palette to the syntax palette, so a debuggee's own coloured
  `print` came out in syntax colours. Now the pane highlights those source lines
  with the same `judbHighlight` the editors use (`frontend/src/lib/highlight.ts`,
  via lezer's `highlightCode` — no new dependency), so **every part of the UI
  that shows Python is themed from one `--tok-*` palette** and a user-supplied
  theme will recolour all of them at once. ANSI stays what it actually is —
  terminal colour — with its own honest 16-colour theme palette
  (`frontend/src/lib/ansi.ts`).
- **[DECISION] realised as *uncaught only*.** The default policy — break on
  *uncaught* exceptions (like pdb post-mortem) — is what shipped, since breaking
  on every `raise` is noise in scientific code full of caught exceptions. The
  `user_exception` bdb hook stays a *no-op* (bdb only calls it while stepping, so
  pausing there would fire on caught exceptions too). The toggle for "break on
  all raised" is **deferred**: doing it without noise needs bdb trace-surgery
  (keeping the local trace live through `continue` and stopping at just the raise
  point) that isn't worth destabilising the well-tested continue path for now.
- *Exit met:* `pytest --pdbcls=judb:Debugger` on a failing test lands paused at
  the assertion frame and the console evaluates the failing expression's operands
  (ws-tested); `-m judb` on a crashing script lands post-mortem on the raising
  frame (ws-tested); plus `tests/test_tracebacks.py` for the chain shape, store
  and pane vitest, and a Playwright exception-pane test that asserts the
  traceback's `raise` has the *same computed colour* as the Source pane's and
  that clicking a frame retargets the Variables pane.
- **Bug found on the way:** `Anser.ansiToHtml` does not escape HTML, so a
  debuggee printing markup wrote into judb's own page. `frontend/src/lib/ansi.ts`
  is now the single entry point for ANSI and escapes first (shipped in 0.1.0 —
  hence a `fixed` fragment of its own).

### B3. Watch expressions — ✅ Done

A **Watch** pane of user-entered expressions, re-evaluated in the selected frame.
It shares a column with Variables (both answer "what is this value right now";
the difference is that a watch is *pinned* and *runs code*).

- **Backend.** A `set_watches(exprs)` command and a `watches` message
  (`{expr, repr, summary}` or `{expr, error}` per row). `Console.watch` evaluates
  the expression in the frame's own globals/locals — deliberately *not* the
  console's scratch namespace, so a watch means the same thing whether or not a
  cell has been run — and formats the value with the shell's display formatter,
  the same one the Variables tree uses (a watched DataFrame carries its HTML
  table). The debugger re-emits `watches` on its own after every pause,
  `select_frame`, and `execute_cell`; when nothing is watched the ordinary pause
  path costs nothing.
- **The invariant, as planned:** watches *do* run user code, unlike the Variables
  tree. Failures are caught per expression (a name that isn't in *this* frame is
  normal while stepping and must not blank the pane), and evaluation is marked as
  an interruptible window (`_executing`) — otherwise a watch stuck in a loop
  would wedge the debugger with no way to remove it, since the wedged thread is
  the one that would have to process the removal.
- **[DECISION] the browser owns the list.** It persists to `localStorage` and
  re-sends the whole list on every connect; the backend only holds what it was
  last told. That makes reconnect, refresh, *and* the next run of the same
  program all work through one path, with no settings layer and no bidirectional
  sync (cf. B5) — and it is the natural warm-up for Phase 3a's saved cells.
- *Exit met:* watch `scale * 10`, step / select another frame, and the pane
  follows — ws-tested (`test_watches_evaluate_in_the_selected_frame`,
  `test_watches_refresh_on_every_pause`), plus store vitest and a Playwright test
  covering frame retargeting, per-row errors, and survival of a reload.
- **Bug found on the way:** the reconnect replay rebuilt the *pause* snapshot, so
  a refresh after selecting a frame came back showing the innermost frame while
  the backend still targeted the selected one (cells and inspection silently
  disagreed with the UI). The server now folds `frame_selected` into the cached
  snapshot, as it already did for `breakpoints`.

### B4. Multi-file source navigation — ✅ Done

Today the Source pane always shows the current/selected frame's file
(`_frame_view` → `linecache.getlines`). Two levels of ambition:

- **✅ Breakpoints panel (PR #4).** A **Breakpoints pane** (bottom row, left of the
  call stack) lists every breakpoint across all files, grouped by file, each with
  its cond/temporary/ignore and a per-row remove button that works cross-file (the
  server sends an `all_breakpoints` list — each record carrying its filename — on
  `paused` and on every `breakpoints` reply).
- **✅ Traceback frames are clickable (B2's rework).** A frame in the Exception
  pane that is still on the stack selects it, matched to the `stack` list on
  filename+lineno+function. That covers *within-stack* navigation; it does not
  need `open_file`, because every such file is already reachable via
  `select_frame`.
- **✅ `open_file(path)` → `source`.** Read via `linecache` (so a not-yet-imported
  module works — bdb reads breakpoint lines the same way) and answered with the
  file's *canonical* name plus the breakpoints already in it, so the browser can
  match it against `stack`/`breaks` by equality. An unreadable path comes back as
  an `error` on the reply and surfaces as the dismissable notice, rather than
  swapping the pane to a blank document.
- **✅ Browse mode in the Source pane.** A `viewing` view in the store shadows the
  frame's file; the gutter and *every* breakpoint command act on
  `shownFilename`, so setting a breakpoint in a browsed file needs no separate
  path. The pane grew a thin file bar: the file on screen, an *Open file…* input
  (free-text — the file you haven't reached is by definition in no list — with a
  datalist of the files judb already knows from the stack, breakpoints and
  traceback), and, while browsing, a plain "not the paused frame" badge and a
  *Back to frame* button.
  **[DECISION] the current-line highlight stays honest:** while browsing, nothing
  is executing in that file, so it is cleared and the navigated-to line gets its
  own weaker marker (`setMarkedLine`). Landing on a frame — a new pause, or a
  frame selection — always ends browsing.
- **✅ Click-to-open.** A Breakpoints-pane row opens its own file at its line, and
  an Exception-pane frame that has already *unwound* (a chained cause, which has
  no frame to select) opens its file at the failing line — the navigability the
  pane's rendered source was missing.
- **[OPEN] Deferrable:** a file tree / fuzzy file-open. Nice, but scope-creep toward
  an editor. Still deferred to Phase 4; path-open is shipped.
- *Exit met:* set a breakpoint in a file the debuggee hasn't reached yet,
  `continue`, and stop there — ws-tested end to end
  (`test_open_file_then_break_in_a_file_not_yet_reached`) and again in Playwright
  through the real UI (open by path → gutter → continue → paused in that file),
  plus store vitest for browse mode and the breakpoint routing.

### B5. Settings / configuration — ✅ Done

A minimal config layer rather than a framework: `judb/config.py` is ~200 lines
and the whole surface is three keys.

- **Scope, as planned, plus one.** `open_browser`, `break_on_exception` and
  `figure_format` (`"png"` vs `"interactive"`, i.e. starting where
  `%matplotlib judb` would have left you) — the process-level options, resolved
  on the Python side. The theme and the watch list stay client-side, so **no
  bidirectional settings-sync protocol was invented**, as the [DECISION] asked.
- **`stop_on_entry` joined them** (`--no-stop-on-entry`), which A2's decision #3
  had foreseen as a flag and never built. Off, the program runs at full speed
  and judb takes over only on a crash or a `breakpoint()` — "start it and walk
  away", the complement to `break_on_exception` and the reason to keep
  stop-on-entry as the *default* rather than the only behaviour. It is
  implemented as "swallow the first line event, then `set_continue()`"
  (`Debugger.skip_stop_on_entry`), because `Bdb.run` installs the trace
  function itself; that first event is the only place to turn the entry stop
  into a continue. Consequence, documented in the README: with no breakpoints
  set, bdb's `set_continue` untraces the program (as it already does for any
  Continue with no breakpoints), so a *gutter* breakpoint needs either the
  entry stop or a `breakpoint()` in the code.
- **Layers, most specific first:** an explicit argument (`set_trace(open_browser=
  False)`), a `python -m judb` flag, the nearest `pyproject.toml`'s
  `[tool.judb]`, `$XDG_CONFIG_HOME/judb/config.toml` (judb's own file, so keys
  sit at the top level), the defaults. Merging is **per key**, so a project
  overriding one setting leaves the user's opinion on the others intact.
  Settings resolve **once** and are cached — otherwise a debuggee that
  `chdir`s could make judb answer differently later in the same run.
- **The nearest `pyproject.toml` ends the upward walk**, even when it has no
  `[tool.judb]`: the first one found *is* the project root, and a checkout
  nested in another project must not inherit the outer one's debugger settings.
- **A bad config is a warning, never an exception.** Malformed TOML, an unknown
  key, an ill-typed value: each prints one line to stderr and falls back to the
  default. Refusing to start the debugger over a stale key would be a poor
  trade — the debugger is the thing the user actually asked for. A mistyped
  *command-line flag*, by contrast, exits 2: that one was typed on purpose.
- **`python -m judb` grew its first options** (`--browser/--no-browser`,
  `--stop-on-entry/--no-stop-on-entry`,
  `--break-on-exception/--no-break-on-exception`, `--figure-format`, and a real
  `--help`). Parsing stops at the target, so `python -m judb train.py
  --no-browser` passes `--no-browser` to *train.py* — anything else would make a
  target's flags unusable whenever they collide with judb's. `--` ends judb's
  options explicitly.
- **`break_on_exception = false` reports the crash the way an undebugged run
  would** — the trimmed traceback on the terminal, exit 1 — rather than parking
  an unattended run in post-mortem waiting for a browser nobody will open.
- **`JUDB_NO_BROWSER` stays a hard override** on top of all of it (headless
  boxes, CI, the test suite). It predates the config layer and does one blunt
  thing well; folding it in as just another layer would have made
  `open_browser = true` in a project config re-open tabs on CI.
- *Exit met:* `[tool.judb] break_on_exception = false` in a project is honored —
  tested end-to-end with a real `python -m judb` subprocess, together with a
  second run proving `--break-on-exception` beats that same file
  (`tests/test_config.py`), plus resolution/precedence/validation units, the CLI
  parser's units, a `figure_format = "interactive"` test showing the first
  figure of a session is already a live canvas, and two for `stop_on_entry =
  false` (a script that runs to completion with *no* websocket client at all,
  and a crashing one whose very first pause is the post-mortem).
- **Still open, deferred with B5 shipped: a user theme.** B2's rework made
  `frontend/src/lib/tokens.css` the single place any colour comes from —
  `--tok-*` for every view of Python (source pane, console cells, traceback) and
  `--ansi-*` for terminal output — so a user theme is a set of custom-property
  overrides, not a per-component change. **[OPEN]** where it lives:
  localStorage-only (pure UI, consistent with the existing light/dark toggle and
  with the watch list) vs. `[tool.judb.theme]` so a project can ship one.
  Recommend localStorage first; the Python side deliberately has no colour
  opinion to override.

---

## Cross-cutting

- **Protocol deltas (all additive):** ✅ `set_break` gained
  `cond`/`temporary`/`ignore` and `paused`/`breakpoints` gained `all_breakpoints`
  (PR #4). ✅ `paused` gained an optional `exception` — `{type, message, chain}`,
  where `chain` is the structured traceback from `judb/tracebacks.py` (mirrored
  as `ExceptionInfo`/`ChainedException`/`TracebackFrame` in `protocol.ts`). ✅
  `set_watches` (client→server) and `watches` (server→client), mirrored as
  `WatchValue`/`WatchesMsg`. ✅ `open_file` (client→server) and `source`
  (server→client, mirrored as `SourceMsg`). The additive protocol deltas Wave B
  planned are complete. Mirror each in `judb/protocol.py` **and**
  `frontend/src/protocol.ts`.
- **Tests:** every backend command gets a Python ws test (extend
  `tests/test_debugger.py` or `tests/test_entrypoints.py`); the exception-pause and
  conditional-bp paths get Playwright coverage. Keep `make test` + `make
  frontend-test` + the Playwright e2e green.
- **Docs:** fold the Phase-3 outcomes back into `IMPLEMENTATION_PLAN.md` §5 (as was
  done for 2a) once shipped.

## Explicitly deferred (not Phase 3)

- **Phase 3a — saved/loadable debug cells** (setup/local/project cells). Sequenced
  *after* this; watch-list persistence (B3) is a natural warm-up for it.
- **Phase 4** — `sys.monitoring` fast breakpoints; `ipywidgets`/`%matplotlib
  widget` Comms; data-flow/call-graph pane; real remote & multi-thread/async;
  native JupyterLab-extension frontend; the file-tree browser (B4 stretch).
- **`jupyter-server-proxy` surfacing** (the §5 "optional/cheap" note): keep as an
  opportunistic demo aid, not a Wave-A blocker.

## Open decisions for you

1. **Python floor** (A1): hold at 3.13, or widen to 3.11/3.12 for reach? -> hold at 3.13, 3.12 can be dropped according to SPEC0 in Q4 2026, let's not add support now to drop in a few months. More important: add tests for 3.14.
2. **Core deps** (A1): OK to drop `numpy`/`pandas` from the core runtime install
   (keep in extras)? -> OK to move to extras.
3. **`-m judb` default** (A2): stop-on-entry (pdb-like) vs. run-to-first-
   breakpoint/exception (recommended)? -> stop-on-entry.
4. **Exception policy** (B2): uncaught-only by default (recommended) vs. break-on-
   all-raised? -> uncaught-only by default.
5. **Multi-file ambition** (B4): path-open + breakpoints panel now, file tree
   deferred to Phase 4 — agree? -> yes.
