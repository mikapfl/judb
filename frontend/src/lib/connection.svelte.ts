// The single websocket-backed reactive store. Every pane reads from `conn`;
// commands go out through `conn.send(...)`. See PHASE2_STACK.md §6.

import type {
  Breakpoint,
  BreakpointLocation,
  Command,
  CompletionsMsg,
  ExceptionInfo,
  FrameView,
  LibraryMsg,
  MimeBundle,
  MplMsg,
  NotebookMsg,
  Output,
  SavedCell,
  ServerMsg,
  StackFrame,
  VarChild,
  VarPath,
  WatchValue,
} from "../protocol";

export type Status =
  | "connecting"
  | "paused"
  | "running"
  | "finished"
  | "disconnected";

export interface Cell {
  /** Stable identity so a cell keeps its editor across reorder/insert/delete. */
  id: number;
  code: string;
  outputs: Output[];
  pending: boolean;
  /** Run order shown as `[n]` (like a notebook's execution count); null if the
   *  cell has never been run. */
  count: number | null;
  /** Optional label, carried into the saved file's `# %%` marker line. */
  name?: string;
  /** Materialised from the user's `setup`-tagged library cells: run once at the
   *  start of a session and never written back (they come from the library, so
   *  saving them here would duplicate them every run). */
  setup?: boolean;
}

/** One console tab: the cells pinned to one location (`at`), or Scratch (`""`).
 *  A tab exists only once it *has* cells, so stepping through code you have no
 *  cells for never moves the console (docs/PHASE3A_PLAN.md §4). */
export interface Tab {
  at: string;
  cells: Cell[];
}

/** Whether a cell keyed `at` belongs at `location`: no key matches everywhere,
 *  a file key matches any function in that file, a function key only itself.
 *  Mirrors judb/cells.py's `matches`. */
export function matchesLocation(at: string, location: string): boolean {
  if (!at) return true;
  if (at === location) return true;
  return !at.includes("::") && location.startsWith(`${at}::`);
}

/** The label a tab shows: the function (or file) its key names. */
export function tabTitle(at: string): string {
  if (!at) return "Scratch";
  const [file, qualname] = at.split("::");
  if (!qualname) return file.split("/").pop() ?? file;
  return `${qualname.split(".").pop()}()`;
}

/** A file opened for browsing — one that no frame is in (see `openFile`). */
export interface SourceView {
  filename: string;
  source: string;
  breakpoints: Breakpoint[];
}

/** Cached result of expanding one variable path (keyed by JSON.stringify(path)). */
export interface ExpandState {
  loading: boolean;
  repr?: MimeBundle;
  children?: VarChild[];
  error?: string;
}

/** Reconnect backoff bounds: quick enough that a blip is invisible, capped so a
 *  genuinely dead server is not hammered. */
const RETRY_MIN_MS = 250;
const RETRY_MAX_MS = 5000;

/** How long after the last edit the notebook is written to disk. Long enough
 *  that typing does not chatter, short enough that a debuggee which runs off and
 *  dies takes nothing with it. */
const SAVE_DEBOUNCE_MS = 500;

class Connection {
  status = $state<Status>("connecting");
  filename = $state("");
  lineno = $state(0);
  functionName = $state("");
  source = $state("");
  locals = $state<string[]>([]);
  stack = $state<StackFrame[]>([]);
  selected = $state(0);
  // The notebook: one tab per location that has cells, plus Scratch at index 0.
  // Cells persist across steps (you build up a notebook and re-run cells as you
  // step), so this is never cleared on pause — and since Phase 3a it is saved to
  // disk, so it outlives the debuggee process too.
  tabs = $state<Tab[]>([
    { at: "", cells: [{ id: 0, code: "", outputs: [], pending: false, count: null }] },
  ]);
  active = $state(0);
  // The selected frame's location key, and a tab that matches it but which we
  // did not switch to because an editor had focus (badged instead — judb never
  // moves the console out from under someone's hands).
  locationKey = $state("");
  pendingTab = $state(-1);
  // Whether a cell editor currently has focus (set by NotebookCell).
  editing = $state(false);
  // The two cell libraries, as last read from disk by the server.
  library = $state<{ user: SavedCell[]; project: SavedCell[]; paths: { user: string; project: string } }>(
    { user: [], project: [], paths: { user: "", project: "" } },
  );
  // Breakpoints in the currently-shown file (with their cond/temporary/ignore
  // options). The source pane only ever edits the displayed frame's file, so a
  // flat list (refreshed on every frame change) is enough for the gutter.
  breakpoints = $state<Breakpoint[]>([]);
  // Every breakpoint across all files (with its file), for the breakpoints pane.
  // Refreshed on pause and on any set/clear; the per-file `breakpoints` above is
  // just the slice the gutter of the shown file needs.
  allBreakpoints = $state<BreakpointLocation[]>([]);
  // A transient, user-dismissable message (e.g. a rejected breakpoint). Set by
  // the store, cleared by the user or by the next successful breakpoint action.
  notice = $state<string | null>(null);
  // The exception behind a post-mortem pause (pytest `--pdb`, or `-m judb`
  // catching a crash), or null for an ordinary pause. Set on `paused`, retained
  // across frame selection (same pause), cleared once the debuggee resumes.
  exception = $state<ExceptionInfo | null>(null);
  // Whether the current pause is post-mortem (the program has already unwound,
  // so the resume buttons only leave the debugger — worth signalling in the UI).
  postmortem = $state(false);
  // Lazily-fetched variable subtrees, keyed by JSON.stringify(path). Cleared
  // whenever the targeted frame changes, since locals differ per frame.
  expanded = $state<Record<string, ExpandState>>({});
  // A file being browsed instead of the selected frame's (null = showing the
  // frame). Set by `openFile`, dropped whenever execution moves or another
  // frame is selected — landing somewhere new always wins over browsing.
  viewing = $state<SourceView | null>(null);
  // The line a navigation asked to see (0 = none). Marked and scrolled to, but
  // never presented as the current line: while browsing, nothing is executing.
  markedLine = $state(0);
  // Watch expressions the user pinned, and their values in the selected frame.
  // The list is ours (persisted, re-sent on connect); the values come from the
  // backend, which re-evaluates them on every pause, frame change and cell run.
  // Values are index-aligned with `watches`, but lag it while a round-trip is
  // in flight, so a row matches its value on `expr` (see `watchValue`).
  watches = $state<string[]>([]);
  watchValues = $state<WatchValue[]>([]);

  #ws: WebSocket | null = null;
  // Reconnect backoff state (see `#scheduleReconnect`).
  #retryDelay = RETRY_MIN_MS;
  #retryTimer: ReturnType<typeof setTimeout> | null = null;
  // FIFO of resolvers awaiting `completions` replies. Requests are serialized on
  // the debuggee thread, so replies come back in the order they were sent.
  #pendingCompletions: Array<(m: CompletionsMsg) => void> = [];
  // Next cell id to hand out (0 is the initial cell) and the running execution
  // count shown as `[n]`.
  #nextCellId = 1;
  #execCount = 0;
  // Debounce timer for `save_notebook`.
  #saveTimer: ReturnType<typeof setTimeout> | null = null;
  // Setup cells still waiting for the first pause to run them.
  #pendingSetup: number[] = [];
  // FIFO of cell ids awaiting a `cell_result`. Execution is serialized on the
  // debuggee thread, so results come back in send order — this correlates each
  // result to the cell that asked for it (any cell can be re-run, not just the
  // newest), independent of the notebook's current order.
  #pendingExec: number[] = [];
  // FIFO of `open_file` requests awaiting their `source` reply, so each reply
  // knows which line the click asked to see (replies come back in send order).
  #pendingOpen: Array<{ filename: string; line: number }> = [];
  // Interactive-matplotlib (WebAgg) figures: a handler per live canvas, keyed by
  // figure id, plus a buffer for messages that arrive before the canvas mounts
  // (the backend may send an initial frame before the cell output renders).
  #mplHandlers = new Map<string, (msg: MplMsg) => void>();
  #mplBuffer = new Map<string, MplMsg[]>();

  get paused(): boolean {
    return this.status === "paused";
  }

  /** A console cell is executing (a runaway cell keeps this true) — the window
   *  in which interrupting makes sense. */
  get busy(): boolean {
    return this.tabs.some((tab) => tab.cells.some((c) => c.pending));
  }

  /** Every cell in every tab, for the few things that span the notebook. */
  get allCells(): Cell[] {
    return this.tabs.flatMap((tab) => tab.cells);
  }

  // --- what the Source pane shows -------------------------------------
  //
  // Normally the selected frame's file; while browsing (`viewing`), the opened
  // one. Everything that acts on "the file on screen" — the gutter, the
  // breakpoint commands — goes through these, so the two cases share one path.

  get browsing(): boolean {
    return this.viewing !== null;
  }

  get shownFilename(): string {
    return this.viewing?.filename ?? this.filename;
  }

  get shownSource(): string {
    return this.viewing?.source ?? this.source;
  }

  get shownBreakpoints(): Breakpoint[] {
    return this.viewing?.breakpoints ?? this.breakpoints;
  }

  /** Every file judb can already name: the stack, the breakpoints, and the
   *  traceback. Offered as suggestions when opening a file — anything else is
   *  still reachable by typing its path. */
  get knownFiles(): string[] {
    const names = new Set<string>();
    for (const frame of this.stack) names.add(frame.filename);
    for (const bp of this.allBreakpoints) names.add(bp.filename);
    for (const entry of this.exception?.chain ?? []) {
      for (const frame of entry.frames) names.add(frame.filename);
    }
    return [...names].sort();
  }

  get location(): string {
    if (this.status === "finished") return "the debuggee has finished";
    if (!this.filename) return "";
    const base = this.filename.split("/").pop() ?? this.filename;
    return `${this.functionName}()  ${base}:${this.lineno}`;
  }

  connect(): void {
    const token = new URLSearchParams(location.search).get("token") ?? "";
    const ws = new WebSocket(`ws://${location.host}/ws?token=${token}`);
    this.#ws = ws;
    ws.onmessage = (ev) => this.#onMessage(JSON.parse(ev.data) as ServerMsg);
    ws.onopen = () => {
      // The server replays the current state to a reconnecting client, so the
      // panes refill on their own; just reset the backoff.
      this.#retryDelay = RETRY_MIN_MS;
      // The watch list is still ours to own, but it is *stored* by the backend
      // now (a random port per run means localStorage cannot outlive the
      // debuggee). The server pushes `notebook` with the restored list; until
      // then, hand over whatever we already have — that covers a reconnect
      // mid-session, where ours is the newer list.
      if (this.watches.length > 0) this.#sendWatches();
    };
    ws.onclose = () => {
      // `finished` means the debuggee is gone for good — nothing to come back
      // to. Anything else (laptop sleep, a network blip, a server hiccup) is
      // worth retrying: the debuggee is very likely still sitting there paused.
      if (this.status === "finished") return;
      this.status = "disconnected";
      this.#scheduleReconnect();
    };
  }

  /** Reconnect with exponential backoff, capped. Keeps retrying: a paused
   *  debuggee can outlive an arbitrarily long disconnect, and a retry against a
   *  dead port fails instantly and cheaply on localhost. */
  #scheduleReconnect(): void {
    if (this.#retryTimer !== null) return; // one in flight is enough
    this.#retryTimer = setTimeout(() => {
      this.#retryTimer = null;
      this.#retryDelay = Math.min(this.#retryDelay * 2, RETRY_MAX_MS);
      this.connect();
    }, this.#retryDelay);
  }

  send(cmd: Command): void {
    this.#ws?.send(JSON.stringify(cmd));
  }

  // --- notebook cells -------------------------------------------------
  //
  // The console is a notebook: cells are editable, re-runnable, and can be
  // added / deleted / reordered. Structural edits (add/delete/move) are purely
  // client-side; only `runCell` talks to the backend.

  /** The cells of the tab in front. Every pane works on "the notebook", which
   *  is whichever tab that is; the tabs themselves are `this.tabs`. */
  get cells(): Cell[] {
    return (this.tabs[this.active] ?? this.tabs[0]).cells;
  }

  set cells(value: Cell[]) {
    (this.tabs[this.active] ?? this.tabs[0]).cells = value;
  }

  /** Insert a new empty cell after `afterId` (or at the end) and return its id. */
  addCell(afterId?: number): number {
    const cell: Cell = {
      id: this.#nextCellId++,
      code: "",
      outputs: [],
      pending: false,
      count: null,
    };
    const at = afterId == null ? -1 : this.cells.findIndex((c) => c.id === afterId);
    if (at < 0) this.cells.push(cell);
    else this.cells.splice(at + 1, 0, cell);
    this.touch();
    return cell.id;
  }

  /** Delete a cell, always keeping at least one (like a notebook). */
  deleteCell(id: number): void {
    const i = this.cells.findIndex((c) => c.id === id);
    if (i < 0) return;
    this.cells.splice(i, 1);
    if (this.cells.length === 0) this.addCell();
    this.touch();
  }

  /** Move a cell one slot up (dir -1) or down (dir +1). */
  moveCell(id: number, dir: -1 | 1): void {
    const i = this.cells.findIndex((c) => c.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= this.cells.length) return;
    const [cell] = this.cells.splice(i, 1);
    this.cells.splice(j, 0, cell);
    this.touch();
  }

  /** Record a cell's current text (called as the editor changes, so a cell you
   *  never ran is still there tomorrow) and schedule a save. */
  editCell(id: number, code: string): void {
    const cell = this.cells.find((c) => c.id === id);
    if (!cell || cell.code === code) return;
    cell.code = code;
    this.touch();
  }

  // --- tabs -----------------------------------------------------------
  //
  // One tab per location key that has cells, Scratch (`at: ""`) always first.
  // Landing somewhere with a tab brings it to the front; landing anywhere else
  // changes nothing, so the console only follows the frame once the user has
  // pinned something (docs/PHASE3A_PLAN.md §4).

  /** Bring tab `index` to the front. */
  selectTab(index: number): void {
    if (index < 0 || index >= this.tabs.length || index === this.active) return;
    this.active = index;
    if (this.pendingTab === index) this.pendingTab = -1;
    this.touch();
  }

  /** The tab for `at`, created (empty, with one blank cell) if there is none. */
  tabFor(at: string): number {
    const found = this.tabs.findIndex((t) => t.at === at);
    if (found >= 0) return found;
    this.tabs.push({ at, cells: [] });
    return this.tabs.length - 1;
  }

  /** Pin one cell to `at`, moving it into that tab (and following it there).
   *  This is the *only* way a cell acquires a location key. */
  pinCell(id: number, at: string): void {
    const from = this.active;
    const i = this.cells.findIndex((c) => c.id === id);
    if (i < 0 || this.tabs[from].at === at) return;
    const [cell] = this.cells.splice(i, 1);
    if (this.cells.length === 0 && from === 0) this.addCell();
    const target = this.tabFor(at);
    this.tabs[target].cells.push(cell);
    this.#dropEmptyTabs();
    this.active = this.tabs.findIndex((t) => t.at === at);
    this.touch();
  }

  /** Pin the whole tab in front to `at` (Scratch keeps a fresh blank cell). */
  pinTab(at: string): void {
    const from = this.active;
    if (this.tabs[from].at === at) return;
    const moving = this.tabs[from].cells;
    if (from === 0) this.tabs[0].cells = [];
    const target = this.tabFor(at);
    this.tabs[target].cells.push(...moving);
    if (from !== 0) this.tabs.splice(from, 1);
    else this.addCell();
    this.#dropEmptyTabs();
    this.active = this.tabs.findIndex((t) => t.at === at);
    this.touch();
  }

  /** Discard a location tab and its cells. Scratch cannot be closed. */
  closeTab(index: number): void {
    if (index <= 0 || index >= this.tabs.length) return;
    this.tabs.splice(index, 1);
    if (this.active >= this.tabs.length) this.active = this.tabs.length - 1;
    else if (this.active > index) this.active -= 1;
    this.touch();
  }

  /** Drop location tabs that have gone empty (Scratch always stays). */
  #dropEmptyTabs(): void {
    this.tabs = this.tabs.filter((tab, i) => i === 0 || tab.cells.length > 0);
  }

  /** Run a cell's current code against the paused frame. Records the code, marks
   *  the cell pending, and correlates the eventual result back to *this* cell. */
  runCell(id: number, code: string): void {
    const cell = this.#findCell(id);
    if (!cell) return;
    cell.code = code; // persist the latest edit even if we can't run it
    this.touch();
    if (!this.paused || !code.trim()) return;
    cell.pending = true;
    cell.outputs = [];
    cell.count = ++this.#execCount;
    this.#pendingExec.push(id);
    this.send({ cmd: "execute_cell", code });
  }

  selectFrame(index: number): void {
    if (!this.paused || index === this.selected) return;
    this.send({ cmd: "select_frame", index });
  }

  // --- source navigation ----------------------------------------------
  //
  // Show any file, not just one the debuggee is stopped in — which is what
  // makes a breakpoint in code it hasn't reached yet possible to set at all.

  /** Show `filename` in the Source pane, scrolled to `line` (0 = top). Opening
   *  the selected frame's own file just marks the line; the pane stays on the
   *  frame (with its current-line highlight) rather than entering browse mode. */
  openFile(filename: string, line = 0): void {
    if (!filename) return;
    this.#pendingOpen.push({ filename, line });
    this.send({ cmd: "open_file", filename });
  }

  /** Leave browse mode and go back to the selected frame's file. */
  backToFrame(): void {
    this.viewing = null;
    this.markedLine = 0;
  }

  // --- breakpoints ----------------------------------------------------
  //
  // The gutter toggles a line: set it if absent, clear it if present. The
  // backend replies with a `breakpoints` message that refreshes the list.
  // All of these act on the file *on screen*, which may be a browsed one.
  toggleBreak(line: number): void {
    if (!this.shownFilename) return;
    const cmd = this.breakpointAt(line) ? "clear_break" : "set_break";
    this.send({ cmd, filename: this.shownFilename, line });
  }

  /** The breakpoint set on `line` in the shown file, or undefined. */
  breakpointAt(line: number): Breakpoint | undefined {
    return this.shownBreakpoints.find((b) => b.line === line);
  }

  // Set (or update) a breakpoint with condition/temporary/ignore options. An
  // empty condition means unconditional; re-setting an existing line updates it.
  setBreak(line: number, opts: Omit<Breakpoint, "line"> = {}): void {
    if (!this.shownFilename) return;
    this.send({
      cmd: "set_break",
      filename: this.shownFilename,
      line,
      cond: opts.cond ?? null,
      temporary: opts.temporary ?? false,
      ignore: opts.ignore ?? 0,
    });
  }

  // Clear a breakpoint. Defaults to the shown file (the gutter); the breakpoints
  // pane passes an explicit filename to clear one in any file.
  clearBreak(line: number, filename: string = this.shownFilename): void {
    if (!filename) return;
    this.send({ cmd: "clear_break", filename, line });
  }

  // --- interrupt ------------------------------------------------------
  //
  // Fire a KeyboardInterrupt into the debuggee thread to stop a runaway cell.
  // Delivered out-of-band by the server (it bypasses the command queue the
  // busy debuggee thread isn't draining), so no state changes here.
  interrupt(): void {
    if (!this.busy) return;
    this.send({ cmd: "interrupt" });
  }

  // --- interactive matplotlib (WebAgg) --------------------------------
  //
  // A mounted canvas registers a handler for its figure id; the store replays
  // any messages that arrived before it mounted. `sendMplEvent` forwards a
  // browser-side canvas event (zoom/pan/draw/…) to the figure on the backend.
  registerMpl(id: string, handler: (msg: MplMsg) => void): () => void {
    this.#mplHandlers.set(id, handler);
    const buffered = this.#mplBuffer.get(id);
    if (buffered) {
      this.#mplBuffer.delete(id);
      for (const msg of buffered) handler(msg);
    }
    return () => this.#mplHandlers.delete(id);
  }

  sendMplEvent(id: string, content: unknown): void {
    this.send({ cmd: "mpl_event", id, content });
  }

  sendMplDownload(id: string, format: string): void {
    this.send({ cmd: "mpl_download", id, format });
  }

  // --- lazy variable inspection ---------------------------------------
  //
  // A pane calls expand(path) to fetch a variable's repr + children; the result
  // lands in `expanded` (keyed by the path) and the pane reads it back. Retrying
  // is allowed only after an error — a loaded/loading entry is left alone.
  expand(path: VarPath): void {
    if (!this.paused) return;
    const key = JSON.stringify(path);
    const cur = this.expanded[key];
    if (cur && !cur.error) return;
    this.expanded[key] = { loading: true };
    this.send({ cmd: "expand", path });
  }

  collapse(path: VarPath): void {
    delete this.expanded[JSON.stringify(path)];
  }

  expansionOf(path: VarPath): ExpandState | undefined {
    return this.expanded[JSON.stringify(path)];
  }

  // --- watch expressions ----------------------------------------------
  //
  // Expressions the user pinned, re-evaluated by the backend in the selected
  // frame on every pause / frame change / cell run. Unlike the Variables tree
  // (which only reads real objects), a watch *runs user code* — that's the
  // point of it, and why the list is explicit rather than inferred.

  /** Append an expression (ignoring blanks and exact duplicates). */
  addWatch(expr: string): void {
    const trimmed = expr.trim();
    if (!trimmed || this.watches.includes(trimmed)) return;
    this.watches.push(trimmed);
    this.#syncWatches();
  }

  /** Replace the expression at `index`; an emptied one is removed. */
  setWatch(index: number, expr: string): void {
    if (index < 0 || index >= this.watches.length) return;
    const trimmed = expr.trim();
    if (trimmed === this.watches[index]) return;
    // Emptying a row deletes it; editing it into a duplicate of another row
    // collapses the two (rows are keyed by expression, so they must be unique).
    if (!trimmed || this.watches.includes(trimmed)) {
      this.removeWatch(index);
      return;
    }
    this.watches[index] = trimmed;
    this.#syncWatches();
  }

  removeWatch(index: number): void {
    if (index < 0 || index >= this.watches.length) return;
    this.watches.splice(index, 1);
    this.#syncWatches();
  }

  /** The value for the row at `index`, or undefined while it is in flight.
   *  Matched on the expression so a just-edited row shows "…" instead of the
   *  previous expression's value. */
  watchValue(index: number): WatchValue | undefined {
    const value = this.watchValues[index];
    return value?.expr === this.watches[index] ? value : undefined;
  }

  /** Persist the list and tell the backend. Sending the whole list (rather than
   *  add/remove deltas) keeps the two ends trivially in sync. */
  #sendWatches(): void {
    this.send({ cmd: "set_watches", exprs: [...this.watches] });
  }

  #syncWatches(): void {
    // Persistence rides along: the server writes the list it is told next to
    // the saved notebook, which (unlike localStorage, scoped to a port that
    // changes every run) is still there for the next run of the program.
    this.#sendWatches();
  }

  // --- persistence ----------------------------------------------------
  //
  // The browser owns the notebook; the backend owns its durability. Saving is
  // debounced and handled by the *server* thread, so a debuggee that runs off
  // and dies without pausing again takes nothing with it
  // (docs/PHASE3A_PLAN.md §1, §8).

  /** Note that the notebook changed; the write itself is debounced. */
  touch(): void {
    if (this.#saveTimer !== null) clearTimeout(this.#saveTimer);
    this.#saveTimer = setTimeout(() => {
      this.#saveTimer = null;
      this.send({
        cmd: "save_notebook",
        cells: this.#flatten(),
        active: this.tabs[this.active]?.at ?? "",
      });
    }, SAVE_DEBOUNCE_MS);
  }

  /** The notebook as the file sees it: flat, each cell carrying its tab's key.
   *  Setup cells are left out — they are materialised from the library on every
   *  connect, so saving them here would duplicate them run after run. */
  #flatten(): SavedCell[] {
    return this.tabs.flatMap((tab) =>
      tab.cells
        .filter((cell) => !cell.setup && cell.code.trim())
        .map((cell) => ({ code: cell.code, name: cell.name ?? "", at: tab.at, tags: [] })),
    );
  }

  #findCell(id: number): Cell | undefined {
    for (const tab of this.tabs) {
      const cell = tab.cells.find((c) => c.id === id);
      if (cell) return cell;
    }
    return undefined;
  }

  #newCell(code = "", name = "", setup = false): Cell {
    return { id: this.#nextCellId++, code, outputs: [], pending: false, count: null, name, setup };
  }

  /** Nothing typed yet: one empty Scratch cell and no other tab. */
  get #untouched(): boolean {
    return (
      this.tabs.length === 1 &&
      this.tabs[0].cells.length === 1 &&
      !this.tabs[0].cells[0].code.trim()
    );
  }

  /** Take up the notebook the last run left behind.
   *
   *  Guarded by what actually matters rather than by "have we done this
   *  already": nothing on screen is ever overwritten, so a *re*connect (where
   *  the live notebook is the newer one) simply finds nothing to do. */
  #adoptNotebook(msg: NotebookMsg): void {
    if (this.watches.length === 0 && msg.watches.length > 0) {
      this.watches = [...msg.watches];
      this.#sendWatches(); // the debugger holds only what it was last told
    }
    if (!this.#untouched || msg.cells.length === 0) return;

    const tabs: Tab[] = [{ at: "", cells: [] }];
    for (const saved of msg.cells) {
      const at = saved.at ?? "";
      let tab = tabs.find((t) => t.at === at);
      if (!tab) tabs.push((tab = { at, cells: [] }));
      tab.cells.push(this.#newCell(saved.code, saved.name));
    }
    if (tabs[0].cells.length === 0) tabs[0].cells.push(this.#newCell());
    this.tabs = tabs;
    const wanted = tabs.findIndex((t) => t.at === msg.active);
    this.active = wanted >= 0 ? wanted : 0;
  }

  /** Follow the selected frame, if a tab is pinned there. */
  #noteLocation(location: string): void {
    this.locationKey = location;
    const match = location ? this.tabs.findIndex((t) => t.at === location) : -1;
    if (match < 0 || match === this.active) {
      this.pendingTab = -1;
      return;
    }
    // Never move the console out from under someone's hands: badge the tab and
    // let them (or the next pause) make the switch.
    if (this.editing) {
      this.pendingTab = match;
      return;
    }
    this.active = match;
    this.pendingTab = -1;
  }

  // --- the cell libraries ---------------------------------------------

  /** Replace one library file; the server echoes the new `library` back. */
  #saveLibrary(scope: "user" | "project", cells: SavedCell[]): void {
    this.send({ cmd: "save_cells", scope, cells });
  }

  /** Save the tab in front into a library, as the cells for its location.
   *
   *  A *replace* of that location's entries rather than an append, so pressing
   *  it twice does not double them: what is on screen is what that location's
   *  cells are. */
  saveTabTo(scope: "user" | "project"): void {
    const tab = this.tabs[this.active];
    if (!tab) return;
    const mine = tab.cells
      .filter((cell) => !cell.setup && cell.code.trim())
      .map((cell) => ({ code: cell.code, name: cell.name ?? "", at: tab.at, tags: [] }));
    if (mine.length === 0) return;
    this.#saveLibrary(scope, [
      ...this.library[scope].filter((cell) => cell.at !== tab.at),
      ...mine,
    ]);
  }

  /** Drop a cell from a library. */
  removeFromLibrary(scope: "user" | "project", index: number): void {
    this.#saveLibrary(
      scope,
      this.library[scope].filter((_, i) => i !== index),
    );
  }

  /** Re-key a library cell to where we are now — the manual answer to a rename
   *  (judb never guesses which function your cells moved to). */
  rekeyLibraryCell(scope: "user" | "project", index: number): void {
    this.#saveLibrary(
      scope,
      this.library[scope].map((cell, i) => (i === index ? { ...cell, at: this.locationKey } : cell)),
    );
  }

  /** Put a library cell into the notebook (at the end of the tab in front). */
  insertFromLibrary(cell: SavedCell): number {
    const added = this.#newCell(cell.code, cell.name);
    this.cells.push(added);
    this.touch();
    return added.id;
  }

  /** The library cells that belong where we are now, richest key first. */
  matchingHere(scope: "user" | "project"): { cell: SavedCell; index: number }[] {
    return this.library[scope]
      .map((cell, index) => ({ cell, index }))
      .filter(({ cell }) => matchesLocation(cell.at, this.locationKey));
  }

  /** Materialise the user's `setup` cells into Scratch and run them once. */
  #materialiseSetup(): void {
    const setup = this.library.user.filter((cell) => cell.tags?.includes("setup"));
    const already = this.tabs[0].cells.some((cell) => cell.setup);
    if (setup.length === 0 || already) return;
    const made = setup.map((cell) => this.#newCell(cell.code, cell.name, true));
    this.tabs[0].cells.unshift(...made);
    this.#pendingSetup = made.map((cell) => cell.id);
    this.#runPendingSetup();
  }

  /** Run the materialised setup cells, once the debuggee is actually paused. */
  #runPendingSetup(): void {
    if (!this.paused || this.#pendingSetup.length === 0) return;
    const ids = this.#pendingSetup;
    this.#pendingSetup = [];
    for (const id of ids) {
      const cell = this.#findCell(id);
      if (cell) this.runCell(id, cell.code);
    }
  }

  // --- tab completion -------------------------------------------------
  //
  // Resolves with the backend's `completions` reply. If the debuggee is not
  // paused (or resumes before the reply), it resolves empty so the editor's
  // async completion source never hangs.
  complete(code: string, cursor: number): Promise<CompletionsMsg> {
    if (!this.paused) {
      return Promise.resolve({ type: "completions", from: cursor, matches: [] });
    }
    return new Promise((resolve) => {
      this.#pendingCompletions.push(resolve);
      this.send({ cmd: "complete", code, cursor });
    });
  }

  #flushCompletions(): void {
    const pending = this.#pendingCompletions;
    this.#pendingCompletions = [];
    for (const resolve of pending) resolve({ type: "completions", from: 0, matches: [] });
  }

  #onMessage(msg: ServerMsg): void {
    switch (msg.type) {
      case "paused":
        this.status = "paused";
        this.stack = msg.stack ?? [];
        this.selected = msg.selected ?? this.stack.length - 1;
        this.expanded = {};
        this.allBreakpoints = msg.all_breakpoints ?? [];
        this.exception = msg.exception ?? null;
        this.postmortem = msg.postmortem ?? false;
        this.#showFrame(msg);
        // Setup cells wait for a frame to run in; this is the first one.
        this.#runPendingSetup();
        break;
      case "frame_selected":
        this.selected = msg.index;
        this.expanded = {};
        this.#showFrame(msg);
        break;
      case "running":
        this.status = "running";
        // The pause is over; the crash banner belongs to that pause only.
        this.exception = null;
        this.postmortem = false;
        this.#flushCompletions();
        break;
      case "finished":
        this.status = "finished";
        this.#flushCompletions();
        // Nothing will complete a still-spinning cell now; release them.
        this.#pendingExec = [];
        for (const cell of this.allCells) cell.pending = false;
        break;
      case "cell_result":
        this.#attachResult(msg.outputs ?? []);
        break;
      case "expanded":
        this.expanded[JSON.stringify(msg.path)] = {
          loading: false,
          repr: msg.repr,
          children: msg.children,
          error: msg.error,
        };
        break;
      case "source": {
        const asked = this.#pendingOpen.shift();
        if (msg.error) {
          // Unreadable path: say so and stay where we are, rather than swapping
          // the pane to a blank document.
          this.notice = msg.error;
          break;
        }
        this.notice = null;
        this.markedLine = asked?.line ?? 0;
        // Asking for the frame's own file is a scroll, not a detour.
        this.viewing =
          msg.filename === this.filename
            ? null
            : {
                filename: msg.filename,
                source: msg.source,
                breakpoints: msg.breakpoints,
              };
        break;
      }
      case "watches":
        this.watchValues = msg.watches;
        break;
      case "notebook":
        this.#adoptNotebook(msg);
        break;
      case "library":
        this.library = { user: msg.user, project: msg.project, paths: msg.paths };
        this.#materialiseSetup();
        break;
      case "completions":
        this.#pendingCompletions.shift()?.(msg);
        break;
      case "mpl": {
        // Deliver to the figure's canvas, or buffer until it mounts.
        const handler = this.#mplHandlers.get(msg.id);
        if (handler) handler(msg);
        else (this.#mplBuffer.get(msg.id) ?? this.#mplBuffer.set(msg.id, []).get(msg.id)!).push(msg);
        break;
      }
      case "breakpoints":
        // A set/clear reply. On a rejected line (`error`), `breakpoints` simply
        // omits it, so the gutter dot never appears — that missing dot is the
        // feedback. Only refresh the gutter if the reply is for the file the
        // source pane is showing (a clear from the breakpoints pane may target
        // another file); the pane's own list always refreshes.
        if (msg.filename === this.filename) this.breakpoints = msg.breakpoints;
        if (this.viewing?.filename === msg.filename) {
          this.viewing.breakpoints = msg.breakpoints;
        }
        this.allBreakpoints = msg.all_breakpoints;
        // Surface a rejected breakpoint as a dismissable notice; a successful
        // set/clear clears any lingering one.
        this.notice = msg.error ?? null;
        break;
      case "error":
        // Surface protocol errors as a synthetic error output on the last cell.
        this.#attachResult([
          { kind: "error", data: { ename: "ProtocolError", evalue: msg.message } },
        ]);
        break;
    }
  }

  // Point the source / location / variables panes at a frame (the innermost on
  // pause, or the one just selected). Landing on a frame always ends browsing:
  // where the debuggee *is* outranks what you were reading.
  #showFrame(view: FrameView): void {
    this.viewing = null;
    this.markedLine = 0;
    this.filename = view.filename;
    this.lineno = view.lineno;
    this.functionName = view.function;
    this.source = view.source ?? "";
    this.locals = view.locals ?? [];
    this.breakpoints = view.breakpoints ?? [];
    this.#noteLocation(view.location ?? "");
  }

  // Attach a result to the cell that requested it (FIFO — see `#pendingExec`).
  // A stray result (e.g. a protocol `error` with nothing queued) falls back to
  // the last cell so it isn't silently dropped.
  #attachResult(outputs: Output[]): void {
    const id = this.#pendingExec.shift() ?? this.cells.at(-1)?.id;
    const cell = id == null ? undefined : this.#findCell(id);
    if (cell) {
      cell.outputs = outputs;
      cell.pending = false;
    }
  }
}

export const conn = new Connection();
