# Phase 3 — Detailed Plan ("Fit & finish", shipping first)

*Companion to `IMPLEMENTATION_PLAN.md` §5. That file's Phase 3 bullet is a
grab-bag; this doc turns it into an ordered, concrete plan grounded in the
Phase-2a codebase. Same conventions: **[DECISION]** = recommended but open,
**[OPEN]** = needs your steer, each work item lists its **touchpoints** and an
**exit** check.*

## 0. Shape: two waves, ship first

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
- **Python floor.** `requires-python = ">=3.13"` is aggressive for a tool that
  wants adoption. **[OPEN]** Is 3.13 a hard floor, or should we widen to 3.11/3.12
  to reach more scientific users? (Costs: verifying no 3.13-only syntax/stdlib use;
  `sys.monitoring` is 3.12+ anyway and is Phase 4.)

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
- **CHANGELOG + version.** Bump to a real `0.1.0` release; decide public
  versioning story.
- **[DECISION] Publish to PyPI as `0.1.0`.** Test-PyPI dry run first, then real.
  This is the concrete "show the world" deliverable and the natural Wave A exit.

**Wave A exit:** a stranger runs `pip install judb`, then either
`python -m judb their_script.py` or `pytest --pdbcls=judb:Debugger` (or adds
`judb.set_trace()`), and lands in the browser UI — with no checkout, no Node, no
`make frontend`.

---

## Wave B — Deepen the debugger

Each item is a small protocol addition + a pane/affordance. Keep `src/protocol.ts`
in sync with `judb/protocol.py` (the hand-mirrored contract).

### B1. Conditional / temporary / ignore-count breakpoints

Nearly free — `bdb.set_break(filename, lineno, temporary, cond, funcname)` already
supports all of it; `_toggle_break` (`debugger.py:295`) just hard-codes none of the
options through.

- Backend: extend the `set_break` command with optional `cond` (string),
  `temporary` (bool), and an `ignore` count; pass them to `set_break` / `set_ignore`.
- Frontend: the gutter breakpoint gains a small editor (right-click / click-hold on
  the gutter marker → a popover for the condition & options). Echo condition state
  back in the `breakpoints` message so the gutter can show conditional bps
  distinctly.
- *Exit:* a breakpoint with `cond="i == 3"` fires only on that iteration; a
  temporary bp auto-clears after firing once — both covered by a ws test.

### B2. Break-on-exception & post-mortem

The stubs are `user_return`/`user_exception` (`debugger.py:65-75`), both `pass`.
This is what makes the **pytest-failure** and `-m judb` "catch the crash" workflows
real, so it's the top Wave-B item and couples with A2.

- Implement `user_exception` to enter `interaction()` at the raising frame, carrying
  the traceback, with the UI flagging "paused on exception" and the console able to
  inspect the crash's locals. Add a `post_mortem(traceback)` path for pytest/`-m`.
- **[DECISION]** Default policy: break on *uncaught* exceptions only (like pdb
  post-mortem), with a toggle for "break on all raised". Breaking on every `raise`
  is noisy in scientific code full of caught exceptions.
- Frontend: distinguish an exception-pause visually; show the exception type/message
  in the status area; keep the traceback available as a cell-style rich output.
- *Exit:* `pytest --pdbcls=judb:Debugger` on a failing test lands paused at the
  assertion frame, and the console can evaluate the failing expression's operands.

### B3. Watch expressions

A pane (or a section of Variables) of user-entered expressions, re-evaluated in the
selected frame on every pause and after each cell run.

- Backend: a `set_watches(list[str])` command + a `watches` result message that
  reuses `Console.inspect`/the display formatter to return a mime-bundle repr per
  expression (so a watched DataFrame shows its HTML table, matching Variables). Runs
  on the debuggee thread like `expand`, against `self._frames[self._selected]`.
- **Invariant to respect:** watches evaluate expressions, which *can run user code*
  (a `@property`, `__repr__`). That's acceptable for an explicit watch (unlike the
  Variables tree, which deliberately never runs user code) — but document it and
  guard failures per-expression (one bad watch must not blank the pane).
- Frontend: a small editable list; re-request on `paused`/`frame_selected`/
  `cell_result`. Persist the watch list across pauses (and consider persisting it
  like Phase 3a cells).
- *Exit:* add `df.shape` as a watch; step; the pane updates each stop.

### B4. Multi-file source navigation

Today the Source pane always shows the current/selected frame's file
(`_frame_view` → `linecache.getlines`). Two levels of ambition:

- **Cheap (recommend for Phase 3):** clicking a stack frame already retargets to its
  file (works). Add: breakpoints list/panel showing all bps across files, click-to-
  open; and open-any-file-by-path (so you can set a breakpoint in a not-yet-hit
  file before `continue`). Source is read via `linecache`, so serving an arbitrary
  project file is a new `open_file(path)` → `source` message.
- **[OPEN] Deferrable:** a file tree / fuzzy file-open. Nice, but scope-creep toward
  an editor. Recommend deferring the tree to Phase 4; ship path-open + breakpoints
  panel now.
- *Exit:* set a breakpoint in a file the debuggee hasn't reached yet, `continue`,
  and stop there.

### B5. Settings / configuration

No config layer exists. Introduce a minimal one rather than a big framework.

- **Scope for Phase 3:** just the settings that unblock the above — default figure
  format (inline PNG vs `%matplotlib judb`), break-on-exception policy, theme
  (already persisted client-side), and browser-auto-open. **[DECISION]** Source of
  truth: a small `pyproject.toml [tool.judb]` / `~/.config/judb` reader on the
  Python side for process-level options, plus the existing client-side localStorage
  for pure-UI prefs. Avoid inventing a bidirectional settings-sync protocol now.
- *Exit:* `[tool.judb] break_on_exception = false` in a project is honored.

---

## Cross-cutting

- **Protocol deltas (all additive):** client→server `set_break` gains
  `cond`/`temporary`/`ignore`; new `set_watches`, `open_file`; server→client new
  `watches`, and `paused` gains an optional exception payload. Mirror each in
  `judb/protocol.py` **and** `frontend/src/protocol.ts`.
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
