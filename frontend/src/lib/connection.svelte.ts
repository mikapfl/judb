// The single websocket-backed reactive store. Every pane reads from `conn`;
// commands go out through `conn.send(...)`. See PHASE2_STACK.md §6.

import type {
  Breakpoint,
  BreakpointLocation,
  Command,
  CompletionsMsg,
  ExceptionInfo,
  FrameView,
  MimeBundle,
  MplMsg,
  Output,
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

/** Where the watch list is persisted. The *browser* owns the list — the backend
 *  only holds whatever it was last told — so it survives a refresh, and a fresh
 *  run of the same debuggee comes up watching the same expressions. */
const WATCHES_KEY = "judb-watches";

function loadWatches(): string[] {
  try {
    const raw = localStorage.getItem(WATCHES_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed.filter((e) => typeof e === "string") : [];
  } catch {
    return []; // absent, malformed, or localStorage unavailable — start empty.
  }
}

class Connection {
  status = $state<Status>("connecting");
  filename = $state("");
  lineno = $state(0);
  functionName = $state("");
  source = $state("");
  locals = $state<string[]>([]);
  stack = $state<StackFrame[]>([]);
  selected = $state(0);
  // The notebook: an ordered list of editable cells, starting with one empty
  // cell. Cells persist across steps (you build up a notebook and re-run cells
  // as you step), so this is never cleared on pause.
  cells = $state<Cell[]>([{ id: 0, code: "", outputs: [], pending: false, count: null }]);
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
  watches = $state<string[]>(loadWatches());
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
    return this.cells.some((c) => c.pending);
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
      // The watch list lives here, not on the backend, so hand it over: this
      // covers a refresh, a reconnect, *and* a brand-new debuggee that has
      // never heard of these expressions. Answered with a `watches` message
      // (once the debuggee is paused), which refills the values.
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
    return cell.id;
  }

  /** Delete a cell, always keeping at least one (like a notebook). */
  deleteCell(id: number): void {
    const i = this.cells.findIndex((c) => c.id === id);
    if (i < 0) return;
    this.cells.splice(i, 1);
    if (this.cells.length === 0) this.addCell();
  }

  /** Move a cell one slot up (dir -1) or down (dir +1). */
  moveCell(id: number, dir: -1 | 1): void {
    const i = this.cells.findIndex((c) => c.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= this.cells.length) return;
    const [cell] = this.cells.splice(i, 1);
    this.cells.splice(j, 0, cell);
  }

  /** Run a cell's current code against the paused frame. Records the code, marks
   *  the cell pending, and correlates the eventual result back to *this* cell. */
  runCell(id: number, code: string): void {
    const cell = this.cells.find((c) => c.id === id);
    if (!cell) return;
    cell.code = code; // persist the latest edit even if we can't run it
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
    try {
      localStorage.setItem(WATCHES_KEY, JSON.stringify(this.watches));
    } catch {
      // best-effort persistence (private mode / sandboxed iframe)
    }
    this.#sendWatches();
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
        for (const cell of this.cells) cell.pending = false;
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
  }

  // Attach a result to the cell that requested it (FIFO — see `#pendingExec`).
  // A stray result (e.g. a protocol `error` with nothing queued) falls back to
  // the last cell so it isn't silently dropped.
  #attachResult(outputs: Output[]): void {
    const id = this.#pendingExec.shift() ?? this.cells.at(-1)?.id;
    const cell = id == null ? undefined : this.cells.find((c) => c.id === id);
    if (cell) {
      cell.outputs = outputs;
      cell.pending = false;
    }
  }
}

export const conn = new Connection();
