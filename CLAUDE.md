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

**Current status: Phase 0 complete.** The two proven halves (console + debugger)
exist and compose; there is no websocket server or browser frontend yet — those
are Phase 1. The `scripts/demo.py` terminal driver stands in for the browser.

## Commands

- `make test` — run all tests (`uv run pytest`).
- `make lint` — run all pre-commit hooks across the repo (ruff check+format, ty, uv-lock).
- `make help` — list make targets (self-documenting from `## ` comments).
- `uv run pytest tests/test_phase0.py::test_stop_run_cell_in_frame_get_png` — run a single test.
- `uv run pytest -s` — run tests showing the captured PNG-byte-count prints.
- `uv run ty check` — type-check only.
- `uv run python scripts/demo.py` — **interactively** drive the debugger in a terminal
  (must be a real TTY for stdin). At each pause, type Python to run it in the paused frame.

Use `uv` for everything (deps live in `pyproject.toml`; `uv sync` to install  - or use `uv add` directly).
pre-commit is installed as a git hook, so commits are gated on the same checks as `make lint`.

## Architecture — the crux

Two well-understood halves glued in one process, with a queue seam between the
debuggee and (eventually) the web server:

- **`judb/console.py`** — an embedded IPython shell (`InteractiveShell.instance()`,
  **not** a ZMQ/ipykernel) that runs a cell and captures rich output as Jupyter
  **mime bundles**. The recipe: custom `DisplayHook`/`DisplayPublisher` subclasses
  append outputs to a module-level `_capture` buffer; `matplotlib` uses the inline
  backend + `select_figure_formats(shell, {"png"})` + `flush_figures()` so any
  figure a cell creates becomes an `image/png` bundle. Cells run against the paused
  frame by injecting `frame.f_globals`/`f_locals` into the shell namespace.
- **`judb/debugger.py`** — a `bdb.Bdb` subclass whose interaction loop is
  **driven by queues** rather than urwid keypresses (pudb's model otherwise). On
  stopping, the debuggee thread enters `interaction()`, emits a `paused` message on
  `outbound`, and blocks on `inbound.get()`. `execute_cell` runs a console cell
  against the paused frame; `step`/`next`/`continue`/`return`/`quit` set bdb state
  and *return* from the loop to unblock the debuggee.
- **`judb/protocol.py`** — `Output`/`CellResult` dataclasses. Outputs deliberately
  use the Jupyter mime-bundle shape (dict keyed by mime type) so the future
  frontend can render them with standard tooling (`@jupyterlab/rendermime`) with
  **zero backend change**. This mime-bundle format is a load-bearing contract; keep it.

### Two invariants that are easy to break

- **Threading model.** Cells execute on the *debuggee thread* (the one that's
  paused), because touching frame state and matplotlib/thread-local state must
  happen there. The interaction loop blocks that thread on `inbound.get()`. In
  Phase 1 the queues get fed by a websocket server running on a *separate daemon
  thread* (see the plan's §2 ASCII diagram). Do not move cell execution off the
  debuggee thread.
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
