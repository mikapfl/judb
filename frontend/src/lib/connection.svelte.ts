// The single websocket-backed reactive store. Every pane reads from `conn`;
// commands go out through `conn.send(...)`. See PHASE2_STACK.md §6.

import type {
  Command,
  CompletionsMsg,
  FrameView,
  MimeBundle,
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
  code: string;
  outputs: Output[];
  pending: boolean;
}

/** Cached result of expanding one variable path (keyed by JSON.stringify(path)). */
export interface ExpandState {
  loading: boolean;
  repr?: MimeBundle;
  children?: VarChild[];
  error?: string;
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
  cells = $state<Cell[]>([]);
  // Lazily-fetched variable subtrees, keyed by JSON.stringify(path). Cleared
  // whenever the targeted frame changes, since locals differ per frame.
  expanded = $state<Record<string, ExpandState>>({});

  #ws: WebSocket | null = null;
  // FIFO of resolvers awaiting `completions` replies. Requests are serialized on
  // the debuggee thread, so replies come back in the order they were sent.
  #pendingCompletions: Array<(m: CompletionsMsg) => void> = [];

  get paused(): boolean {
    return this.status === "paused";
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
    ws.onclose = () => {
      if (this.status !== "finished") this.status = "disconnected";
    };
  }

  send(cmd: Command): void {
    this.#ws?.send(JSON.stringify(cmd));
  }

  execute(code: string): void {
    if (!code.trim()) return;
    this.cells.push({ code, outputs: [], pending: true });
    this.send({ cmd: "execute_cell", code });
  }

  selectFrame(index: number): void {
    if (!this.paused || index === this.selected) return;
    this.send({ cmd: "select_frame", index });
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
  }

  // Cell execution is serialized on the debuggee thread, so the newest pending
  // cell is the one this result belongs to.
  #attachResult(outputs: Output[]): void {
    for (let i = this.cells.length - 1; i >= 0; i--) {
      if (this.cells[i].pending) {
        this.cells[i].outputs = outputs;
        this.cells[i].pending = false;
        return;
      }
    }
  }
}

export const conn = new Connection();
