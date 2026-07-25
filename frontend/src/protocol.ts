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

/** A breakpoint in the source file, with its bdb options. Mirrors
 *  debugger.py's `_file_breaks` records. */
export interface Breakpoint {
  /** 1-based line number. */
  line: number;
  /** Condition expression that must be truthy to fire, or null/absent. */
  cond?: string | null;
  /** Whether the breakpoint clears itself after firing once. */
  temporary?: boolean;
  /** Remaining hits to skip before firing (0 = fire immediately). */
  ignore?: number;
}

/** A breakpoint plus the file it lives in — for the global breakpoints pane,
 *  which spans every file, not just the currently-shown one. */
export interface BreakpointLocation extends Breakpoint {
  filename: string;
}

/** Per-frame fields shared by `paused` and `frame_selected`. */
export interface FrameView {
  filename: string;
  lineno: number;
  function: string;
  locals: string[];
  source: string;
  /** Breakpoints set in this frame's file. */
  breakpoints: Breakpoint[];
}

/** One traceback frame: where it is, and a window of source around the failing
 *  line. The backend ships no colour — the pane highlights `lines` with the
 *  editors' own theme (see lib/highlight.ts). Mirrors judb/tracebacks.py. */
export interface TracebackFrame {
  filename: string;
  /** 1-based line that was executing in this frame. */
  lineno: number;
  /** The enclosing function's name (`"<module>"` at module level). */
  function: string;
  /** The source window; may be empty when the file could not be read. */
  lines: string[];
  /** 1-based line number of `lines[0]`. */
  first_lineno: number;
  /** Fine-grained anchor into the failing line (`~~~^~~~`), when known. */
  col?: number;
  end_col?: number;
}

/** One exception in a chain: what it was, and where it came from. */
export interface ChainedException {
  /** The exception's *qualified* class name, the way Python prints it (bare for
   *  builtins: `"ValueError"`; `"mod.Cls"` for others). */
  type: string;
  /** What `traceback.format_exception_only` prints — `"Type: message"`, plus a
   *  SyntaxError's caret line and any `__notes__`. */
  headline: string[];
  /** Innermost-last frames of this exception's own traceback. */
  frames: TracebackFrame[];
  /** How this follows the previous chain entry: `raise ... from ...`
   *  (`"cause"`) or raised while handling it (`"context"`). Absent on the
   *  first entry. */
  relation?: "cause" | "context";
}

/** The exception behind a post-mortem pause, for the Exception pane. */
export interface ExceptionInfo {
  /** The exception's class name, e.g. `"ValueError"`. */
  type: string;
  /** Its `str()` — the message. */
  message: string;
  /** The exception chain, oldest cause first (the way Python prints it). */
  chain: ChainedException[];
}

export interface PausedMsg extends FrameView {
  type: "paused";
  stack: StackFrame[];
  /** Index into `stack` of the initially-targeted (innermost) frame. */
  selected: number;
  /** Every breakpoint across all files (for the breakpoints pane). */
  all_breakpoints: BreakpointLocation[];
  /** Set when the program has already unwound (pytest `--pdb`, `-m judb`
   *  catching a crash): resume just leaves the debugger. */
  postmortem?: boolean;
  /** Set when paused at the outermost frame's return — the debuggee is done. */
  exiting?: boolean;
  /** Present on an exception pause: what crashed. */
  exception?: ExceptionInfo;
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

/** One watch expression's current value in the selected frame. Either it
 *  evaluated (`repr`/`summary`) or it didn't (`error`) — a watch that names
 *  something out of scope in this frame is normal while stepping. */
export interface WatchValue {
  /** The expression, echoed back so the pane can match it to its row. */
  expr: string;
  /** The value's mime bundle (a watched DataFrame carries its HTML table). */
  repr?: MimeBundle;
  /** A one-line `type  repr` summary, as in the Variables tree. */
  summary?: string;
  /** Why it could not be evaluated, e.g. `"NameError: name 'df' is not defined"`. */
  error?: string;
}

/** Reply to `set_watches`, and pushed on its own whenever the values may have
 *  changed: a new pause, a frame selection, or a console cell run. */
export interface WatchesMsg {
  type: "watches";
  watches: WatchValue[];
}

/** Reply to `complete`: `matches` are full replacements for the doc range
 *  `[from, cursor)` (absolute offsets), the shape CodeMirror autocomplete wants. */
export interface CompletionsMsg {
  type: "completions";
  from: number;
  matches: string[];
}

/** Reply to `set_break`/`clear_break`: the file's remaining breakpoints
 *  (so the gutter can redraw), plus `error` if bdb rejected the line. */
export interface BreakpointsMsg {
  type: "breakpoints";
  filename: string;
  breakpoints: Breakpoint[];
  /** Every breakpoint across all files (for the breakpoints pane). */
  all_breakpoints: BreakpointLocation[];
  error?: string;
}

/** An interactive-matplotlib (WebAgg) message for figure `id`, en route from the
 *  figure's canvas to its browser client: either a JSON control message (`json`)
 *  or a base64-encoded PNG frame (`blob`). See judb/mpl_backend.py. */
export interface MplMsg {
  type: "mpl";
  id: string;
  json?: unknown;
  blob?: string;
  /** A figure rendered by the backend for saving (base64), in reply to a
   *  `mpl_download` command — png/svg/pdf/… (the WebAgg canvas is raster, so
   *  vector formats must come from the server). */
  download?: { format: string; data: string };
  download_error?: string;
}

/** Cell-output mime carrying `{ id }` — mounts an interactive WebAgg canvas. */
export const WEBAGG_MIME = "application/vnd.judb.webagg+json";

export type ServerMsg =
  | PausedMsg
  | FrameSelectedMsg
  | RunningMsg
  | FinishedMsg
  | CellResultMsg
  | ExpandedMsg
  | WatchesMsg
  | CompletionsMsg
  | BreakpointsMsg
  | MplMsg
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
  /** The full watch list (the browser owns it and always sends it whole). */
  | { cmd: "set_watches"; exprs: string[] }
  | { cmd: "complete"; code: string; cursor: number }
  | {
      cmd: "set_break";
      filename: string;
      line: number;
      cond?: string | null;
      temporary?: boolean;
      ignore?: number;
    }
  | { cmd: "clear_break"; filename: string; line: number }
  | { cmd: "mpl_event"; id: string; content: unknown }
  | { cmd: "mpl_download"; id: string; format: string }
  | { cmd: "interrupt" };
