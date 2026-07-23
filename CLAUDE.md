# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What judb is

A browser-based visual debugger for scientific Python: it combines pudb-style
stepping with a notebook-style rich console that executes cells **in the
currently-paused stack frame**. The point is to plot/inspect a paused frame's
real objects (DataFrames, arrays, matplotlib figures) the way you would in a
notebook — something neither pdb/pudb nor a papermill-wrapped notebook does well.

Read `REQUIREMENT_ANALYSIS.md` (motivation) and `IMPLEMENTATION_PLAN.md`
(architecture, design decisions, phased roadmap) before making non-trivial
changes. The plan is the source of truth for *why* things are shaped the way they
are; its §5 defines the phases and each phase's exit criterion.

**Current status: Phase 2 complete (four-pane app, the MVP).** Phase 1 (vertical
slice) is complete: `judb.set_trace()` starts a localhost websocket server
(`judb/server.py`) and opens a browser page served from `judb/static/index.html`.
That page is now the **built Svelte SPA** (source in `frontend/`, see below), not
the old hand-written HTML: a four-pane layout (Source / Variables / Console / Call
stack) with a CodeMirror source view (current-line highlight), an in-frame
CodeMirror console rendering text/html/png/svg/etc., a call-stack list, and
continue/next/step/return/quit. Frame selection (`select_frame` retargets the console), lazy
variable inspection (`expand` → a mime-bundle repr + one level of navigable
children, rendered as a tree in the Variables pane), tab-completion
(`complete` → IPython completer matches wired to CodeMirror autocomplete), a
clickable breakpoint gutter (`set_break`/`clear_break`; a set breakpoint fires on
the next `continue`), and interrupting a runaway console cell (`interrupt`, sent
from the server thread since the busy debuggee thread can't drain the command
queue: a real SIGINT for a main-thread debuggee — breaks blocking C calls like
`time.sleep`, as Ctrl+C does — else `PyThreadState_SetAsyncExc`, which only breaks
pure-Python execution) are all done. Next up is Phase 3 (fit & finish — see
`IMPLEMENTATION_PLAN.md` §5).

## Commands

- `make test` — run all tests (`uv run pytest`).
- `make lint` — run all pre-commit hooks across the repo (ruff check+format, ty, uv-lock).
- `make changelog-draft` — preview release notes (consumes nothing; CI builds the real ones).
- `make help` — list make targets (self-documenting from `## ` comments).
- `uv run pytest tests/test_server.py::test_plot_paused_frame_over_websocket` — run a single test.
- `uv run pytest -s` — run tests showing the captured PNG-byte-count prints.
- `uv run ty check` — type-check only.
- `uv run python scripts/demo_p2.py` — run a demo debuggee and drive it from the
  browser UI (`scripts/demo_rich.py` for a spread of rich objects).

Use `uv` for everything (deps live in `pyproject.toml`; `uv sync` to install  - or use `uv add` directly).
pre-commit is installed as a git hook, so commits are gated on the same checks as `make lint`.

### CI and releases (`.github/workflows/`)

`ci.yml` runs on every pull request and every push to `main`, in four parallel
jobs: **tests** (matrix py3.13 + py3.14 — no Node: `hatch_build.py` skips the
frontend build for *editable* installs, and the Python suite does not need the
bundle), **lint + licenses** (`pre-commit` + `pylic`, synced with
`--all-extras` because ty must resolve `demo_rich.py`'s imports), **frontend**
(svelte-check, Vitest, Playwright — needs Python too, since the e2e drives a
real debuggee), and **package** (`scripts/smoke_install.sh`: build wheel+sdist,
install each into a fresh venv, round-trip it).

**Changelog.** Every user-visible change adds a fragment to `changelog.d/`
(`<id>.<type>.md`, types `added`/`changed`/`fixed`/`removed`/`docs`, or an empty
`<pr>.misc.md` for changes with nothing to tell users). CI enforces this on PRs
via `towncrier check`. Fragments never collide, so branches in flight don't
fight over the changelog. **You never run `make changelog` by hand** — the
release workflow runs it for a `pypi` release and commits the result back;
`make changelog-draft` is a preview that consumes nothing. See
`changelog.d/README.md` and `RELEASING.md`.

**Version.** Single source of truth is `__version__` in `judb/__init__.py`;
hatchling and towncrier both read it, so a release bumps exactly one line.

`release.yml` is **`workflow_dispatch` only** — never a push or tag trigger. It
takes a `target` input (`testpypi` | `pypi`), rebuilds and re-verifies the
artifacts, then publishes via PyPI **Trusted Publishing** (OIDC, no stored
token). Each target maps to a GitHub *environment* of the same name, so `pypi`
can require a reviewer. The one-time setup (GitHub environments + trusted
publishers) and the release procedure itself are in **`RELEASING.md`**.

### Tests (`tests/`, organised by topic)

Files are named for **what they cover**, not the phase that introduced them:

- `test_console.py` — the embedded IPython console: mime-bundle capture,
  matplotlib-inline, `inspect` for the Variables pane.
- `test_server.py` — websocket transport + token auth, and the whole stack over
  one socket (pause → plot in-frame → PNG → continue).
- `test_debugger.py` — driving a paused debuggee: frames (`select_frame`,
  `expand`, `complete`), breakpoints, interrupts, and signals (a real terminal
  Ctrl+C over a pty).
- `test_entrypoints.py` — how users start judb: `pytest --pdbcls` (post-mortem,
  `--trace`, `breakpoint()`), `python -m judb` (script and `-m module`), and
  `set_trace` hardening.
- `test_mpl_backend.py` — the `%matplotlib judb` WebAgg backend.
- `helpers.py` — shared drivers (`ws_url`, `recv_type`, `read_judb_url`,
  `read_pty_for_url`, …). Imported as `from helpers import ...`: `tests/` has no
  `__init__.py`, so pytest's prepend import mode puts it on `sys.path`.

Prefer adding to the matching topic over creating a new file. Entry points and
anything involving signals or a controlling terminal need a **real subprocess**
(and a pty) — in-process tests silently miss those paths.

### Frontend (`frontend/`, Svelte 5 + Vite + pnpm)

Use **pnpm** (pinned via `packageManager` + corepack — run `corepack enable` once;
never `npm install` here). Targets:

- `make frontend` — build the SPA into `judb/static/index.html` (a **single inlined
  file** via `vite-plugin-singlefile`). The built bundle is **gitignored, not
  committed** (it polluted every diff). Run `make frontend` once after checkout / any
  `frontend/` change so the server has something to serve; a `hatch_build.py`
  hatchling hook regenerates it at package-build time, and the sdist ships the
  pre-built bundle so `pip install` from PyPI still needs no Node.
- `make frontend-check` — `svelte-check` (TS + Svelte types).
- `make frontend-test` — Vitest unit tests (renderers, store).
- `make frontend-e2e` — Playwright browser test; needs `make frontend` first and
  a one-time `cd frontend && pnpm exec playwright install chromium`.
- `make frontend-install` — `pnpm install`.

The stack and its resolved decisions live in `PHASE2_STACK.md` — read it before
changing the frontend's shape.

## Architecture — the crux

Two well-understood halves glued in one process, with a queue seam between the
debuggee and (eventually) the web server:

- **`judb/console.py`** — an embedded IPython shell (`InteractiveShell.instance()`,
  **not** a ZMQ/ipykernel) that runs a cell and captures rich output as Jupyter
  **mime bundles**. The recipe: custom `DisplayHook`/`DisplayPublisher` subclasses
  append outputs to a module-level `_capture` buffer; `matplotlib` uses the inline
  backend + `select_figure_formats(shell, {"png"})` + `flush_figures()` so any
  figure a cell creates becomes an `image/png` bundle. Cells run against the paused
  frame by injecting `frame.f_globals`/`f_locals` into the shell namespace. Two more
  frame-namespace services live here because they reuse the shell: `complete(code,
  cursor, frame)` (the IPython completer, `use_jedi=False` for deterministic
  fragment→replacement pairs) and `inspect(frame, path)` for the Variables pane —
  it resolves a `["name",…]`/attr/item/index path against the frame's *real* objects
  (never running user code) and returns the value's mime-bundle repr (via the shell's
  display formatter, so a DataFrame → HTML table) plus one level of children.
- **`judb/debugger.py`** — a `bdb.Bdb` subclass whose interaction loop is
  **driven by queues** rather than urwid keypresses (pudb's model otherwise). On
  stopping, the debuggee thread enters `interaction()`, emits a `paused` message on
  `outbound`, and blocks on `inbound.get()`. `execute_cell` runs a console cell
  against the paused frame; `select_frame`/`expand`/`complete` retarget/inspect/
  complete against the *selected* frame (`self._frames[self._selected]`);
  `step`/`next`/`continue`/`return`/`quit` set bdb state and *return* from the loop
  to unblock the debuggee.
- **`judb/protocol.py`** — `Output`/`CellResult` dataclasses. Outputs deliberately
  use the Jupyter mime-bundle shape (dict keyed by mime type) so the future
  frontend can render them with standard tooling (`@jupyterlab/rendermime`) with
  **zero backend change**. This mime-bundle format is a load-bearing contract; keep it.
- **`judb/server.py`** — aiohttp server on a daemon thread bridging the debugger's
  queues to a WebSocket (browser→`inbound.put`, `outbound`→browser). Localhost +
  random port + random URL token (mandatory: it runs arbitrary code). The seam is
  *only* the queues; the server never executes cells. Note: `outbound.get()` is
  drained on a **dedicated daemon thread**, not `run_in_executor` — the default
  executor's non-daemon workers block interpreter shutdown when parked in a get()
  that never returns.
- **`frontend/`** — the Svelte 5 SPA (build-only source; the *built* output lives
  in `judb/static/`). `src/lib/connection.svelte.ts` is the one websocket-backed
  runes store every pane reads; `src/protocol.ts` hand-mirrors `judb/protocol.py`
  (keep them in sync). Rich output goes through `src/lib/Output.svelte`, an
  **ordered `mime → renderer` registry** (richest-first) — adding
  `@jupyterlab/rendermime` later is one registry entry, not a rewrite. Script-bearing
  `text/html` renders in a `sandbox="allow-scripts"` iframe; images use `data:` URIs.
  The Variables pane is a recursive `panes/VarNode.svelte` tree over an `expand`
  cache in the store (keyed by path, cleared on frame change); console cells get
  tab-completion via an async CodeMirror source backed by `conn.complete()` (a
  FIFO-correlated `complete`→`completions` round-trip). The backend contract is
  unchanged: same queues, same mime bundles.

### Two invariants that are easy to break

- **Threading model.** Cells execute on the *debuggee thread* (the one that's
  paused), because touching frame state and matplotlib/thread-local state must
  happen there. The interaction loop blocks that thread on `inbound.get()`; the
  websocket server (`server.py`) feeds those queues from a *separate daemon thread*
  (the plan's §2 ASCII diagram). Do not move cell execution off the debuggee thread.
- **`_capture` is module-level in `console.py`.** The hook/publisher instances are
  created by IPython, so a module global is the seam they append to. This is safe
  only because cell execution is serialized on the debuggee thread — don't
  parallelize cell execution without rethinking this.

## Conventions

- Framework-imposed signatures (IPython `DisplayHook`/`DisplayPublisher`,
  `bdb.Bdb` hooks) are **fully annotated** to match/tighten the base class; ty is
  strict (`missing-type-argument = "error"`). Genuinely heterogeneous `Any`
  (mime-bundle payloads: `first_of`, `evaluate`) is allowed only with an
  inline `# noqa: ANN401`.
- ruff runs with `extend-select = ["ANN", "PYI"]` and `preview = true`; `ANN201`
  is disabled for `tests/**` only.
- Since the end of Phase 2a, we're now working in a feature branch workflow.
