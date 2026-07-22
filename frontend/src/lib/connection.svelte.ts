// The single websocket-backed reactive store. Every pane reads from `conn`;
// commands go out through `conn.send(...)`. See PHASE2_STACK.md §6.

import type { Command, FrameView, Output, ServerMsg, StackFrame } from "../protocol";

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

  #ws: WebSocket | null = null;

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

  #onMessage(msg: ServerMsg): void {
    switch (msg.type) {
      case "paused":
        this.status = "paused";
        this.stack = msg.stack ?? [];
        this.selected = msg.selected ?? this.stack.length - 1;
        this.#showFrame(msg);
        break;
      case "frame_selected":
        this.selected = msg.index;
        this.#showFrame(msg);
        break;
      case "running":
        this.status = "running";
        break;
      case "finished":
        this.status = "finished";
        break;
      case "cell_result":
        this.#attachResult(msg.outputs ?? []);
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
