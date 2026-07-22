// Wire protocol, hand-mirrored from judb/protocol.py + debugger.py message shapes.
// Kept in sync manually (small surface). See PHASE2_STACK.md §6/§7.

/** A Jupyter-style mime bundle: mime type -> payload (base64 for images). */
export type MimeBundle = Record<string, unknown>;

export interface Output {
  kind: "execute_result" | "display_data" | "stream" | "error";
  data: MimeBundle & {
    // stream
    name?: "stdout" | "stderr";
    text?: string;
    // error
    ename?: string;
    evalue?: string;
    traceback?: string[];
  };
  metadata?: Record<string, unknown>;
}

export interface StackFrame {
  filename: string;
  lineno: number;
  function: string;
}

// --- server -> client ---------------------------------------------------

export interface PausedMsg {
  type: "paused";
  filename: string;
  lineno: number;
  function: string;
  locals: string[];
  source: string;
  stack: StackFrame[];
}

export interface RunningMsg {
  type: "running";
}

export interface FinishedMsg {
  type: "finished";
}

export interface CellResultMsg {
  type: "cell_result";
  success: boolean;
  outputs: Output[];
}

export interface ErrorMsg {
  type: "error";
  message: string;
}

export type ServerMsg =
  | PausedMsg
  | RunningMsg
  | FinishedMsg
  | CellResultMsg
  | ErrorMsg;

// --- client -> server ---------------------------------------------------

export type Command =
  | { cmd: "continue" }
  | { cmd: "next" }
  | { cmd: "step" }
  | { cmd: "return" }
  | { cmd: "quit" }
  | { cmd: "execute_cell"; code: string };
