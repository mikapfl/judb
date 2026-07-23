// The single websocket-backed reactive store. Every pane reads from `conn`;
// commands go out through `conn.send(...)`. See PHASE2_STACK.md §6.

import type {
  Command,
  CompletionsMsg,
  FrameView,
  MimeBundle,
  MplMsg,
  Output,
  ServerMsg,
  StackFrame,
  VarChild,
  VarPath,
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
  // 1-based line numbers with a breakpoint in the currently-shown file. The
  // source pane only ever edits the displayed frame's file, so a flat list
  // (refreshed on every frame change) is enough for the gutter.
  breakpoints = $state<number[]>([]);
  // Lazily-fetched variable subtrees, keyed by JSON.stringify(path). Cleared
  // whenever the targeted frame changes, since locals differ per frame.
  expanded = $state<Record<string, ExpandState>>({});

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

  // --- breakpoints ----------------------------------------------------
  //
  // The gutter toggles a line: set it if absent, clear it if present. The
  // backend replies with a `breakpoints` message that refreshes the list.
  toggleBreak(line: number): void {
    if (!this.filename) return;
    const cmd = this.breakpoints.includes(line) ? "clear_break" : "set_break";
    this.send({ cmd, filename: this.filename, line });
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
        this.#showFrame(msg);
        break;
      case "frame_selected":
        this.selected = msg.index;
        this.expanded = {};
        this.#showFrame(msg);
        break;
      case "running":
        this.status = "running";
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
        // A set/clear reply for the file the source pane is showing. On a
        // rejected line (`error`), `lines` simply omits it, so the gutter dot
        // never appears — that missing dot is the feedback.
        this.breakpoints = msg.lines;
        if (msg.error) console.warn("breakpoint:", msg.error);
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
  // pause, or the one just selected).
  #showFrame(view: FrameView): void {
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
