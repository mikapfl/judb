# judb — High-Level Implementation Plan

*A browser-based visual debugger for scientific Python, combining pudb-style
stepping with a notebook-style rich console that runs in the paused frame.*

See `REQUIREMENT_ANALYSIS.md` for motivation. This document is a **draft to
iterate on** — decisions marked **[DECISION]** have a recommendation but are
open; **[OPEN]** items need your steer.

---

## 1. The core idea (and why it's feasible)

judb is fundamentally **two well-understood halves glued in one process**:

1. **A debugger** (like pudb): a `bdb.Bdb` subclass that traces the debuggee,
   stops at breakpoints, exposes the stack/locals, and does step/next/continue.
2. **A notebook-style rich console**: an embedded IPython shell that executes
   cells **in the namespace of the currently-paused frame**, capturing rich
   output (matplotlib PNG, pandas HTML tables, plotly/altair HTML) as
   Jupyter-format *mime bundles*.

The UI lives in the browser (4 panes: code, variables, stack, console). The
debuggee process hosts a small web server; hitting the first breakpoint opens a
browser tab.

### What I already validated with throwaway experiments

Both halves — and crucially their *composition* — are proven, not assumed:

- **Headless rich capture works.** With a small recipe (custom IPython
  `displayhook_class`/`display_pub_class` + `matplotlib.interactive(True)` +
  `select_figure_formats(shell, {"png"})` + `matplotlib_inline.flush_figures()`),
  running a cell yields correct mime bundles: an `int` → `text/plain`, a
  DataFrame → `text/html` **and** `text/plain`, a plot → `image/png` (~22 KB),
  `print()` → stream, an exception → traceback. No ZMQ kernel needed.
- **The debugger composes with the console in a single thread.** A `bdb`
  subclass stopped inside a running loop (`compute:85`, locals `{n:4, total:0,
  i:0}`, full call stack), ran console cells **against the paused frame's real
  arrays** (a plot of the in-scope `data` rendered to PNG), then `next`/`continue`
  stepped correctly through loop iterations. ~120 lines total.

The remaining work (web server, websocket protocol, browser frontend) is
standard engineering with no research risk.

---

## 2. Architecture

```
  ┌──────────────────────── debuggee process (your script / pytest / module) ─────────────────────┐
  │                                                                                                │
  │   your code ──hits breakpoint()/gutter bp──►  ┌──────────────────────────────┐                │
  │                                               │  judb.debugger (bdb.Bdb)      │                │
  │                                               │  • stop / step / next / cont  │                │
  │                                               │  • stack, locals, breakpoints │                │
  │   (debuggee thread BLOCKS here while paused)  │  • interaction loop:          │                │
  │                                               │      cmd = inbound_q.get()    │                │
  │                                               └──────┬───────────────┬────────┘                │
  │                                        execute_cell  │               │ step/continue           │
  │                                                      ▼               ▼                         │
  │                                          ┌────────────────────┐   (return → run)               │
  │                                          │ judb.console       │                                │
  │                                          │ embedded IPython,  │  mime bundles                  │
  │                                          │ runs in frame ns,  │─────────────┐                  │
  │                                          │ captures rich out  │             │                  │
  │                                          └────────────────────┘             │                  │
  │                                                                             │                  │
  │   ┌───────────────────────────── background daemon thread ─────────────────▼───────────────┐  │
  │   │  judb.server  (async: aiohttp/starlette)                                                │  │
  │   │   • serves static frontend bundle    • WebSocket  ◄── inbound_q / outbound via          │  │
  │   │                                                       loop.call_soon_threadsafe          │  │
  │   └───────────────────────────────────────────────┬─────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────── │ ──────────────────────────────────────────┘
                                                        │ WebSocket (JSON + Jupyter mime bundles)
                                                        ▼
  ┌──────────────────────────────── browser (SPA, localhost) ─────────────────────────────────────┐
  │   [ code + breakpoint gutter ]  [ variables ]   [ call stack ]                                 │
  │   [ notebook-style console: editable, re-runnable cells → rich outputs (PNG/HTML/plotly) ]     │
  └───────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Threading model (the crux).** The debuggee runs single-threaded as usual.
When paused, its thread sits in the debugger's interaction loop doing a blocking
`inbound_q.get()`. A **separate daemon thread** runs the async web server. Messages
flow:

- browser → server → `inbound_q.put(cmd)` → picked up by the paused debuggee thread.
- `execute_cell` is run **on the debuggee thread** (it must, to touch frame state
  and keep matplotlib/thread-local state coherent); results are pushed back with
  `loop.call_soon_threadsafe(ws.send_json, ...)`.
- `step`/`next`/`continue` call `set_step()` etc. and *return* from the loop,
  unblocking the debuggee until the next stop, which re-enters the loop and emits
  a fresh state snapshot. While the debuggee runs between stops, the UI shows a
  "running…" state (stepping/console disabled).

This is exactly pudb's model, with a websocket-fed command queue replacing
urwid keypresses.

**The frontend is a swappable layer.** Everything above the WebSocket — the
debugger, the in-frame console, and the mime-bundle protocol — is the
frontend-agnostic **asset**. The browser SPA is the primary frontend; a
JupyterLab-extension frontend is an optional *alternative* over the same backend,
not a different architecture (see §3, "Why not build judb as a JupyterLab
extension?").

---

## 3. Key design decisions

### [DECISION] Debugger core → `bdb`, with `sys.monitoring` as a later optimization
`bdb` (stdlib, what pdb/pudb build on) is the pragmatic MVP choice. Its
`sys.settrace` tracing is slow on hot loops — a real pain for data science.
Python 3.12+ `sys.monitoring` (PEP 669) enables near-zero-overhead breakpoints
and is a plausible **differentiator vs. pudb**. Recommendation: ship on `bdb`,
keep the tracing behind an interface, evaluate a `sys.monitoring` backend later.
We'll study pudb's `Debugger` for reference (its urwid UI is not reusable).

### [DECISION] Console → lightweight embedded IPython, **not** a ZMQ kernel
Proven recipe (~40 lines) instead of `ipykernel`. A real ZMQ kernel wants to
*own* execution and fights the bdb-blocking model; the lightweight path keeps one
process, one loop, and full control. Cost: we forgo, for now, Jupyter Comms →
`ipywidgets`/`%matplotlib widget` interactivity (stretch goal, §6). We keep the
**mime-bundle data format** identical to Jupyter so frontend rendering stays
standard.

### [DECISION] Wire protocol → WebSocket + JSON carrying Jupyter mime bundles
No ZMQ. A small message set:
- server→client: `paused` (file, line, source, stack, local *names*), `running`,
  `finished`, `cell_result` (list of mime bundles), `frame_selected`, `expanded`
  (a variable's mime-bundle repr + one level of children), `completions`, `error`.
- client→server: `command` (`step|next|continue|return|quit`),
  `set_breakpoint`/`clear_breakpoint`, `select_frame`, `execute_cell`, `expand`,
  `complete`, `interrupt`.
Because outputs are Jupyter mime bundles, we can adopt `@jupyterlab/rendermime`
later with zero backend change.

### [DECISION] Server → one lean async lib (recommend `aiohttp`; alt: `starlette`+`uvicorn`)
Serves the static bundle *and* the websocket from a single dependency, runs in
the daemon thread's event loop. Binds to `127.0.0.1` on a random port with a
**random token in the URL** (it executes arbitrary code — localhost-only + token
is mandatory).

### [DECISION] Frontend → Svelte shell + embedded `@jupyterlab/rendermime`

The frontend question actually has **two independent axes** that are easy to
conflate:

- **App-shell framework** (React vs Svelte): how we build *our own* UI — the
  debugger panes, layout, websocket state wiring.
- **Component reuse** (Lumino / the JupyterLab stack): whether we reuse
  JupyterLab's *actual* notebook widgets (`@jupyterlab/rendermime`,
  `@jupyterlab/outputarea`, `@jupyterlab/cells`, `@jupyterlab/codemirror`), which
  are built on Lumino.

These are **not** mutually exclusive: `@jupyterlab/rendermime` renders a mime
bundle into a plain DOM node, which we can mount inside a React/Svelte component
via a ref. So "reuse Jupyter's rendering" does **not** require adopting Lumino as
the whole app framework. That turns the choice into three tiers, not three rivals:

| | React shell (+`dockview`) | Svelte shell | Full Lumino / JupyterLab stack |
|---|---|---|---|
| Build debugger panes | easy, declarative | easiest, least boilerplate | verbose (imperative widgets) |
| Reuse Jupyter output rendering | embed rendermime via ref | embed rendermime via ref | native, highest fidelity |
| 4-pane dockable layout | `dockview` (very good) | more DIY (`svelte-splitpanes`) | **best-in-class** (`DockPanel`) |
| `ipywidgets` path later | hard | hard | **natural** |
| Contributor pool / hiring | largest | smaller | smallest |
| Bundle size / build complexity | medium | smallest | largest |

**Lumino's genuine draws** are its best-in-class dockable layout (`DockPanel`/
`SplitPanel`) and native reuse of the real notebook widgets incl. the full
`ipywidgets` frontend. **Its costs** are an imperative, non-mainstream,
thinly-documented model; a large, version-coupled dependency tree that is
historically painful to consume *standalone* (outside a JupyterLab extension
build); and the fact that our most distinctive UI — the debugger panes (code +
breakpoint gutter, variable tree, call stack) — are **not** Jupyter components,
so Lumino makes the reuse part cheaper and the custom part more expensive.

**Decision: the middle path — a mainstream shell + embed `rendermime` for
console output.** It captures the highest-value, most-*separable* slice of
Jupyter reuse (full-fidelity, script-safe rich rendering — HTML, LaTeX, vega,
plotly) without inheriting the framework. Our console cell is simpler than a full
Jupyter cell (CM6 editor + run button + output area), so we reuse `rendermime`
specifically, not `@jupyterlab/cells`. **Shell = Svelte** (resolved for Phase 2,
open decision #2): its low-boilerplate DX and lean bundle fit a small team, and
`rendermime`/CodeMirror embed via a node ref just as cleanly as in React. The
accepted cost is a more-DIY dock layout (`svelte-splitpanes` rather than
`dockview`); React stayed the fallback if ecosystem breadth or `ReactWidget`
interop had outweighed that.

- **CodeMirror 6** for the code pane (read-only + breakpoint gutter +
  current-line highlight) and the editable console cells. It's framework-agnostic
  (mount an `EditorView` into a node), so it's **neutral** across all three tiers.
- **Phasing:** the Phase-1 vertical slice can use a ~100-line custom renderer
  (`text/plain` with ANSI→HTML, `text/html`, `image/png`, `svg`, `markdown`,
  `json`; script-bearing plotly/bokeh HTML in a sandboxed `srcdoc` iframe) to
  avoid dragging in the Jupyter build early. ~~Adopt embedded `rendermime` in
  Phase 2 for real fidelity.~~ *(Superseded — `rendermime` was spiked and declined
  in Phase 2a; the lean renderer stayed and the real gaps were filled with small
  targeted libraries. See §5, Phase 2a.)* Backend is unchanged either way (same
  mime bundles).
- **What would change this:** if `%matplotlib widget` / `ipywidgets` becomes a
  must-have (open decision #3), that tugs toward more Jupyter reuse — possibly all
  the way to full Lumino, since their widget frontend is painful to reimplement.
  Full Lumino only wins outright if "pixel-identical to JupyterLab **and** deep
  `ipywidgets` support" is a top-3 goal.

### [DECISION] Entry points — meet users where they already are
- `PYTHONBREAKPOINT=judb.set_trace python script.py` — drop-in `breakpoint()`.
- `python -m judb script.py [args]` — run a script/module under judb.
- `import judb; judb.set_trace()` — explicit.
- `pytest --pdb --pdbcls=judb:Debugger` — pytest already supports a custom debug
  class; this gives "drop into judb on test failure" nearly for free.

### The browser payoff (worth stating)
Because outputs render in a real browser, **interactive plotly / altair / bokeh
"just work"** (they emit HTML/JS). That directly answers the requirement's pain
point (Qt backends cumbersome, dash doesn't work) and is something neither pudb
nor papermill-wrapped notebooks do well.

### Why not build judb as a JupyterLab extension?
Tempting — ride Jupyter's distribution, shell, and *existing* debugger. But the
moment we try to ride Jupyter's **debugger**, we hit the exact wall judb exists to
break. Three "Jupyter-native" models, and where each fails:

- **Ride the stock kernel+DAP debugger.** JupyterLab has had a real visual
  debugger since 3.0, but it's bound to one model: *the kernel runs cells; the
  debugger pauses the kernel; while paused you can only **evaluate**, not run
  cells.* The stock "evaluate at breakpoint" (PR #9930) is a **single text box
  with no rich output** — no plots, no HTML dataframes (explicitly deferred as a
  "severe limitation"). It can't easily be otherwise: while paused the kernel
  thread is blocked (an `input()` there deadlocks it), so new rich cells can't be
  dispatched against the paused frame. The one thing judb is *for* is exactly what
  this model can't do.
- **Debuggee hosts a native ipykernel+debugpy.** Same wall — and it replaces our
  proven ~120-line `bdb`+IPython recipe (which works *because* we own the
  interaction loop) with fragile extensions to debugpy internals.
- **Our backend + a JupyterLab-extension frontend.** This one doesn't hit the
  execution wall (we still own execution), but we *don't* get
  `@jupyterlab/debugger`'s panes for free — they're wired to the kernel-DAP
  service, not our external process — so we still hand-build the debugger panes as
  imperative Lumino widgets and take on the labextension build/version treadmill.

**Decisive point:** the entry points we want — `pytest`, `python script.py`,
papermill, a pipeline module — are all *external processes*, and JupyterLab's
debugger can only debug **its own kernels**. Requiring JupyterLab would forfeit
the headline workflow (and contradict the requirement's own "hit the first
breakpoint → a browser tab opens").

**Conclusion:** the frontend-agnostic **backend is the asset**; the frontend is a
swappable layer. Keep the standalone SPA as primary; treat a JupyterLab-extension
frontend as an *optional, additive distribution channel* (the third model above,
Phase 3/4), with **`jupyter-server-proxy`** as a near-zero-effort way to surface
the SPA inside Lab. Going *fully* Jupyter-native looks like a free lunch but
quietly surrenders the differentiator.

---

## 4. Package layout

```
judb/
  __init__.py     # set_trace(), breakpoint hook, public API
  __main__.py     # `python -m judb script.py`
  debugger.py     # bdb.Bdb subclass + interaction loop (queue-driven)
  console.py      # embedded IPython rich-capture (the proven recipe);
                  #   also lazy variable inspection (Console.inspect: path →
                  #   mime-bundle repr + children) and completion (Console.complete),
                  #   since both reuse the shell's display formatter / completer
  server.py       # async static + websocket server; thread bridge to debugger
  protocol.py     # message dataclasses / (de)serialization
  frontend/       # Vite/Svelte source (lean mime→renderer registry + iframe output)
  static/         # built bundle shipped in the wheel
```

---

## 5. Phased roadmap

**Phase 0 — Skeleton & spikes (partly done).** uv project, deps, fold the two
validated spikes into `console.py` and a minimal `debugger.py`. *Exit:* stop at a
`breakpoint()`, run a cell in-frame in a pytest, see a PNG bundle in the terminal.

**Phase 1 — Vertical slice, ugly but real. ✅ Done.** Minimal websocket protocol
(`judb/server.py`, aiohttp on a daemon thread, localhost + token) + a bare HTML
page (`judb/static/index.html`): code pane with current line, a single console
cell that runs in-frame and renders text/html/png, and continue/step buttons.
*Exit:* set `breakpoint()`, browser opens, plot a paused DataFrame, step,
continue — verified headlessly by `tests/test_phase1.py`.

**Phase 2 — The four-pane app (MVP). ✅ Done.** Svelte+CodeMirror UI;
breakpoint gutter; variables pane (simple + expandable/lazy rich reprs); stack
pane with frame selection (console retargets to the selected frame); multi-cell,
editable, re-runnable console with persistence and tab-completion. Robust
capture (stdout streaming, error tracebacks, interrupt a runaway cell). **This
is the first genuinely useful release.**
- **Done:** the `frontend/` Svelte 5 + Vite + pnpm SPA scaffold (stack settled in
  `PHASE2_STACK.md`), building a single inlined `judb/static/index.html`; four-pane
  splitpanes shell; CodeMirror source (current-line) + editable in-frame console;
  `mime→renderer` output registry; call-stack list; Vitest units + a Playwright
  e2e that reproduces the Phase-1 exit criterion in a real browser.
  `select_frame` (clicking a stack frame retargets console + inspection);
  `expand` (lazy variable inspection — each `expand(path)` returns a mime-bundle
  repr *and* one level of navigable children, rendered as a tree in the Variables
  pane, so a DataFrame shows its HTML table and containers drill down without
  eagerly serializing them); `complete` (IPython completer, jedi-off for
  deterministic fragment/replacement pairs, wired to CodeMirror autocomplete on
  Tab / as-you-type). Each is covered by a Python websocket test and a Playwright
  browser test.
  `set_break`/`clear_break` (a clickable CodeMirror breakpoint gutter — a set
  breakpoint fires on the next `continue`, since bdb keeps tracing while any
  breakpoint exists); and `interrupt` (a runaway console cell is stopped from the
  server thread — the queued command path can't reach the debuggee thread while
  it's busy in the cell — via a real SIGINT when the debuggee is the main thread,
  so it breaks blocking C calls like `time.sleep` the way Ctrl+C does, and via
  `PyThreadState_SetAsyncExc` for a worker-thread debuggee, which only breaks
  pure-Python execution). Each is covered by a Python websocket test and a
  Playwright browser test.

**Phase 2a — Polish & rich-output fidelity. ✅ Largely done.** Make outputs look
like a notebook and the app look finished. All frontend-only — backend and
protocol are unchanged (same mime bundles), which is exactly what the mime-bundle
contract (§3) was meant to buy us.

- **`rendermime` — investigated, declined.** Spiked `@jupyterlab/rendermime`
  end-to-end: it *does* build into the single-file bundle, but it's a poor trade.
  Bundle 474 KB → 1.16 MB (2.4×); it drags in the JupyterLab **app** framework
  (`@jupyterlab/services`, `apputils` + React, `settingregistry`, the full
  `@lumino/widgets` tree), ships an `eval` (`coreutils`) and json5 interop
  warnings, and — worst — *regresses* the browser payoff: its untrusted HTML
  renderer strips `<script>`, so plotly/bokeh render static (or run unsandboxed
  if marked trusted). **Decision: keep the lean `mime→renderer` registry and fill
  the real gaps with small, targeted libraries** (below). Revisit only if
  `ipywidgets` becomes a must-have (which per §3 tugs toward full Lumino anyway).
  *This supersedes the "adopt embedded `rendermime` in Phase 2" wording in §3.*
- **Notebook-fidelity HTML output.** The sandboxed output iframe shipped no
  stylesheet, so pandas' `<table border="1" class="dataframe">` fell through to
  1990s UA table borders. It now injects a compact analogue of Jupyter's output
  CSS — `.dataframe` styling plus generic rules (tables, headings, lists, links,
  code/pre, blockquotes) — so DataFrames and any `_repr_html_` look like a
  notebook cell. Heavy library reprs (pandas `Styler`, xarray, sklearn, plotly)
  ship their own scoped CSS and override these low-specificity rules untouched. A
  `postMessage` resize script fits the frame to its content (was a fixed 24 rem
  gap). The CSS is derived from Jupyter's notebook/nbconvert stylesheets —
  attributed BSD-3-Clause in `NOTICE` + `licenses/jupyter-LICENSE.txt`.
- **Markdown output.** `text/markdown` (previously shown as raw source) is parsed
  with `marked` and rendered through the same sandboxed iframe, reusing its
  isolation (no separate sanitizer) and the Jupyter output CSS.
- **Interactive viz specs.** Vega, Vega-Lite and Plotly JSON mimes
  (`application/vnd.vega*+json`, `application/vnd.plotly.v1+json`) render by
  loading the library from a **CDN inside the sandboxed iframe**
  (`frontend/src/lib/richOutput.ts`) — lazy (only when such an output appears) and
  isolated, so the single-file bundle stays small (**+1.6 KB**, no bundled viz
  libs; plotly.js alone is ~3.5 MB). A fallback *below* image/`text/html`, since
  altair/plotly usually also emit a self-contained `text/html` we prefer (it's
  offline-capable). Trade-off: the JSON-mime path needs network the first time.
  Verified end-to-end (raw vnd outputs → themed charts, light + dark).
- **Light/dark theming.** A full theme system driven by `tokens.css` custom
  properties (dark base + a `:root[data-theme="light"]` override): auto-detect via
  `prefers-color-scheme`, plus a persisted toolbar toggle cycling auto → light →
  dark, applied before mount (no flash). CodeMirror recolours via a CSS-variable
  `HighlightStyle` + explicit selection/caret colours (no editor rebuild); the
  output iframe bakes in per-theme colours. `frontend/src/lib/theme.svelte.ts`.
- **Tests:** `richOutput` + `theme` unit suites and output-precedence / markdown /
  DataFrame-CSS cases (Vitest); Playwright e2e still green (6/6).
- **Still open in 2a:** broader layout/design polish and small usability passes;
  and a committed test for *real* plot interactivity — currently covered only by a
  CDN-render probe, not a checked-in e2e (the viz libraries aren't judb deps).

**Phase 3 — Fit & finish.** Conditional breakpoints, watch expressions, source
across multiple files, save/load console cells (notebook export), settings,
packaging & a clean install story. *Optional/cheap:* surface the standalone SPA
inside JupyterLab via `jupyter-server-proxy` (near-zero effort, gets us "inside
Lab" for demos without the labextension toolchain).

**Phase 4 — Differentiators / stretch.** `sys.monitoring` fast breakpoints;
`ipywidgets`/`%matplotlib widget` via a Comm channel (needs kernel-comm work);
the data-flow / call-graph pane hinted at in the requirements; remote &
multi-thread/async debugging; *optional* native **JupyterLab-extension frontend**
(the third model in §3) as an additional distribution channel over the same backend.

**Proposed MVP line: end of Phase 2.**

---

## 6. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Rich capture from a paused frame is hard | ~~High~~ **Retired** | Proven recipe (§1). |
| Debugger + console don't compose | ~~High~~ **Retired** | Proven in one thread (§1). |
| `settrace` tracing too slow on big loops | Med | Ship bdb; `sys.monitoring` backend in Phase 4. |
| Script-bearing HTML (plotly/bokeh) rendering | Med | Sandboxed `srcdoc` iframe (Phase 1) — **kept**; Phase 2a added Jupyter output CSS, Markdown, and CDN-loaded Vega/Plotly JSON in the same iframe. (`rendermime` was declined — it strips scripts; see Phase 2a.) |
| Writing to frame locals from console | Low | Match pdb: reads see locals, new names live in a scratch ns (documented). |
| Threading/interrupt of a runaway console cell | Med | Interrupt via `KeyboardInterrupt` into the debuggee thread. |
| Arbitrary code execution over the network | Med | Localhost-only + random URL token, no external bind. |
| `ipywidgets` interactivity | Low (deferred) | Explicit non-goal for MVP; needs Comms/kernel (Phase 4). |

---

## 7. Open decisions for you

1. **MVP ambition:** a sharp hackathon-grade Phase-2 prototype, or aim from the
   start at a polished, packaged, installable tool? -> Phase-2 prototype
2. **Frontend stack:** analysis in §3 — middle path (mainstream shell + embedded
   `rendermime`). -> **Resolved: Svelte** shell + CodeMirror 6 for Phase 2
   (lean bundle / low-boilerplate DX; accepted cost is a more-DIY dock layout).
3. **Widget interactivity:** is static-inline matplotlib + native interactive
   plotly/altair enough for MVP, or is `%matplotlib widget` a must-have (which
   pulls a real Comm/kernel channel forward from Phase 4)? -> defer decision until we see what Phase-2 can already do
4. **pytest-first?** Debugging failing tests is a very common data-science loop
   and nearly free via `--pdbcls`. Worth prioritizing as a headline entry point? -> yes
```
