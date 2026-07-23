# Phase 2 — Frontend technology stack

Working document to settle the stack *before* writing code, so we don't build in
a direction we then unwind. Decisions here refine IMPLEMENTATION_PLAN.md §3–§5
(where "Svelte shell + embedded rendermime" is already chosen). Markers:
**[SET]** = follows directly from the plan / no real alternative;
**[REC]** = my recommendation, open to change; **[OPEN]** = needs your call.

The backend does not change: the seam stays the `inbound`/`outbound` queues and
the Jupyter **mime-bundle** contract (`judb/protocol.py`). Everything below is the
`frontend/` source tree that builds into `judb/static/`.

---

## 1. Language & framework

- **Svelte 5 (runes: `$state`/`$derived`/`$effect`)** — **[SET]** per plan. The
  runes model maps cleanly onto "one websocket pushes state, panes react."
- **TypeScript** — **[REC]**. The real payoff is typing the wire protocol
  (`protocol.ts` mirroring `protocol.py`) so the four panes agree on message
  shapes. Cost is ~zero with Vite.
- **No SvelteKit** — **[SET]**. We ship a single static page served by aiohttp,
  not an SSR/routing app. SvelteKit's server half would fight the wheel model.
  Plain Vite + `@sveltejs/vite-plugin-svelte`.

## 2. Build tooling

- **Vite** — **[SET]** (plan §4 already says "Vite/Svelte source"). `build.outDir`
  → `../judb/static`, emptied on build.
- **Package manager: pnpm** — **[DECIDED]**. Chosen for the same strict/fast ethos
  as the Python side (`uv`, `ty`, `ruff`): pnpm enforces that a component can only
  import deps it actually declares (no phantom deps npm allows), plus a
  content-addressed store. Pinned reproducibly via a `"packageManager": "pnpm@x.y.z"`
  field in `frontend/package.json` + `corepack enable` — **no global install**, so
  it doesn't pollute the environment. `frontend/pnpm-lock.yaml` is the lockfile.
  (Package manager only affects the frontend-dev inner loop: end users `pip install`
  the pre-built bundle shipped in the sdist with no Node at all — see §10.)
- **Bundle shape: single inlined file** — **[DECIDED]**. `vite-plugin-singlefile`
  emits one `index.html` with JS/CSS inlined; the server keeps its current one-line
  `FileResponse(static/index.html)`, no new routes. Localhost/single-user makes the
  lack of asset caching a non-issue. Revisit only if we later lazy-load a heavy
  renderer (would push us to a hashed `assets/` dir + a static route).

## 3. Code editing — CodeMirror 6

- **[SET]** (plan). Framework-agnostic (`EditorView` mounted into a node), so it's
  neutral to Svelte and reused for both editors below.
- Packages: `@codemirror/state`, `@codemirror/view`, `@codemirror/commands`,
  `@codemirror/language`, `@codemirror/lang-python`, `@codemirror/autocomplete`.
- **Source pane** (read-only): line-number gutter, **breakpoint gutter**
  (clickable `gutter` + line decorations), **current-line highlight**. Fed by the
  `paused` message's `source`/`lineno`.
- **Console cells** (editable): Python highlighting, history, and **tab-completion
  wired to the backend** (see §7 — a new `complete` round-trip to IPython's
  completer). Autocomplete UI is `@codemirror/autocomplete`.

## 4. Rich output rendering — **[DECIDED] lean renderer first, rendermime behind a seam**

The plan says "adopt embedded `@jupyterlab/rendermime` in Phase 2," but it's the
heaviest / most version-coupled / most build-fragile dep, so we sequence it:

- Port the Phase-1 renderer into a Svelte `<Output>` component backed by a
  **`mime → renderer` registry**: `text/plain` (+ANSI via `anser`),
  `image/png|jpeg`, `image/svg+xml`, `text/markdown`, `application/json`, and
  **script-bearing `text/html` in a sandboxed `srcdoc` iframe** (covers
  plotly/bokeh/altair — the browser payoff).
- Because it's a registry, adopting `@jupyterlab/rendermime` later is **one added
  entry** (e.g. for `application/vnd.*` / LaTeX), not a rewrite — kept as a
  late-Phase-2/Phase-3 upgrade once the four panes work.
- This is a deliberate deviation from the plan's wording; §3 of the plan already
  anticipates it ("a ~100-line custom renderer … adopt embedded `rendermime` in
  Phase 2 for real fidelity"), so update that line's timing when we fold this in.

## 5. Layout — four panes

- **`svelte-splitpanes`** for resizable/collapsible splits — **[SET]** (plan named
  it as the Svelte trade-off vs `dockview`).
- Proposed arrangement **[REC]**:
  ```
  ┌───────────────────────────┬───────────────┐
  │  Source (CodeMirror)      │  Variables    │
  │  + breakpoint gutter      │  (lazy tree)  │
  ├───────────────────────────┼───────────────┤
  │  Console (cells+outputs)  │  Call stack   │
  └───────────────────────────┴───────────────┘
  ```
  Toolbar (continue/next/step/return/quit + status) spans the top. Frame click in
  the stack **retargets the console** to that frame.
- **Not** full dockview/Lumino docking for Phase 2 — resizable splits are enough
  for the MVP; revisit only if users want tear-off panes.

## 6. State, styling, protocol types

- **State**: one `connection.ts` module — a reactive store (runes) holding
  `{status, frame, source, stack, locals, cells}` plus `send(cmd)`; owns the
  WebSocket and reconnection. No Redux/Zustand-equivalent. **[REC]**
- **Styling**: hand-written scoped CSS + a small `tokens.css` (CSS custom
  properties) for the dark theme. **No Tailwind** — avoids a second build opinion
  for a four-pane app. **[REC]**
- **Protocol types**: `frontend/src/protocol.ts`, hand-mirrored from
  `protocol.py`, kept in sync manually (small surface). Codegen is overkill now.
  **[REC]**

## 7. Protocol extensions Phase 2 needs (backend, but shapes the state model)

Not stack choices, but the panes require these messages beyond Phase 1 — listed
so the state model is designed for them up front:

- `paused` gains a **stack** (list of frames) — already partly present.
- `select_frame(index)` → console + source + vars **retarget** to that frame.
- **Lazy variable expansion**: `expand(path)` → children (don't serialize whole
  DataFrames eagerly).
- **Completions**: `complete(code, cursor)` → IPython completer results.
- **Breakpoints**: `set_break`/`clear_break(file, line)` from the gutter.
- **Interrupt** a runaway cell (`KeyboardInterrupt` into the debuggee thread).

## 8. Dev workflow **[OPEN-ish]**

The friction: the aiohttp port is random per run and every request is
token-gated. Two dev modes:

- **(a) Vite dev server + proxy (HMR).** `vite dev` on a fixed port; `server.proxy`
  forwards `/ws` (+ index) to the running judb server. Needs the live host:port +
  token — supply via an env var that judb prints on start, or a fixed dev port +
  token flag. Best inner-loop DX; a little plumbing.
- **(b) `vite build --watch` into `judb/static/`.** No HMR, but zero proxy/token
  plumbing — you just reload the real judb tab. Dead simple.
- **[REC]** start with (b) to get panes on screen fast, add (a) once the churn
  justifies it.

## 9. Testing

- **Vitest + @testing-library/svelte** for renderers and the `connection` store
  (pure logic, mime→DOM). **[DECIDED]**
- **Playwright** e2e driving the *real* server end-to-end (the browser analog of
  `tests/test_server.py`) — **[DECIDED]**: proves the Phase-2 exit criterion in an
  actual browser. Accept the browser dep + CI weight.
- Python-side headless websocket tests stay as the backend contract.

## 10. Packaging into the wheel

- `frontend/` (source) is **not** shipped; `judb/static/` (build output) **is**
  (hatchling already packages `judb/`). **[SET]**
- Build integration **[OPEN]**:
  - **(a) `make frontend`** builds into `static/`, and we **commit** the built
    bundle. Simplest; no Node needed to `pip install` from git. Slight repo noise.
  - **(b) hatchling build hook** runs `pnpm run build` at wheel-build time. Cleaner
    provenance; requires Node in the build env and a `.gitignore`'d `static/`.
  - **[REC]** (a) for now (hackathon-grade Phase 2, installable from git without
    Node), migrate to (b) at packaging time (Phase 3's "clean install story").

---

## Resolved

- **§4 output rendering** → **lean custom renderer first**, rendermime behind a
  registry seam.
- **§2 bundle shape** → **single inlined file** (`vite-plugin-singlefile`).
- **§9 testing** → **Vitest + Playwright** e2e.

## Still-defaulted (speak up to change; otherwise these stand)

- **§8 dev workflow** → start with `vite build --watch` into `static/` (no HMR),
  add the Vite-proxy HMR mode later.
- **§10 packaging** → `make frontend` builds the bundle on demand; it is
  **gitignored, not committed**. A `hatch_build.py` hatchling build hook regenerates
  it at package-build time (wheel/sdist), and the sdist force-includes the pre-built
  bundle so `pip install` needs no Node.
