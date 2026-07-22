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

// --- lazy variable inspection (expand) ----------------------------------

/** One hop from a parent object: how to descend, and the key. The first step of
 *  a path is always `["name", <local>]`. Mirrors console.py's PathStep. */
export type PathStep = ["name" | "attr" | "item" | "index", string | number];
export type VarPath = PathStep[];

/** One level-deep child of an expanded variable. */
export interface VarChild {
  key: string;
  path: VarPath;
  summary: string;
  expandable: boolean;
}

// --- server -> client ---------------------------------------------------

/** Per-frame fields shared by `paused` and `frame_selected`. */
export interface FrameView {
  filename: string;
  lineno: number;
  function: string;
  locals: string[];
  source: string;
}

export interface PausedMsg extends FrameView {
  type: "paused";
  stack: StackFrame[];
  /** Index into `stack` of the initially-targeted (innermost) frame. */
  selected: number;
}

export interface FrameSelectedMsg extends FrameView {
  type: "frame_selected";
  index: number;
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

/** Reply to `expand`: the value's mime bundle + one level of children, or an
 *  error if the path could not be resolved (e.g. the local is gone). */
export interface ExpandedMsg {
  type: "expanded";
  path: VarPath;
  repr?: MimeBundle;
  children?: VarChild[];
  error?: string;
}

/** Reply to `complete`: `matches` are full replacements for the doc range
 *  `[from, cursor)` (absolute offsets), the shape CodeMirror autocomplete wants. */
export interface CompletionsMsg {
  type: "completions";
  from: number;
  matches: string[];
}

export type ServerMsg =
  | PausedMsg
  | FrameSelectedMsg
  | RunningMsg
  | FinishedMsg
  | CellResultMsg
  | ExpandedMsg
  | CompletionsMsg
  | ErrorMsg;

// --- client -> server ---------------------------------------------------

export type Command =
  | { cmd: "continue" }
  | { cmd: "next" }
  | { cmd: "step" }
  | { cmd: "return" }
  | { cmd: "quit" }
  | { cmd: "execute_cell"; code: string }
  | { cmd: "select_frame"; index: number }
  | { cmd: "expand"; path: VarPath }
  | { cmd: "complete"; code: string; cursor: number };
