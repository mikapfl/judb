import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conn, Connection, matchesLocation, type Cell } from "./connection.svelte";
import type { SavedCell } from "../protocol";

const blank = (id: number): Cell => ({
  id,
  code: "",
  outputs: [],
  pending: false,
  count: null,
});

const ids = () => conn.cells.map((c) => c.id);

describe("notebook cell operations", () => {
  beforeEach(() => {
    // Reset to a single empty cell (ids keep counting up — that's fine).
    conn.cells = [blank(-1)];
  });

  it("adds a cell at the end and returns its id", () => {
    const before = conn.cells.length;
    const id = conn.addCell();
    expect(conn.cells.length).toBe(before + 1);
    expect(conn.cells.at(-1)?.id).toBe(id);
  });

  it("inserts a cell directly after the given one", () => {
    const first = conn.cells[0].id;
    const tail = conn.addCell(); // now [first, tail]
    const mid = conn.addCell(first); // insert after first -> [first, mid, tail]
    expect(ids()).toEqual([first, mid, tail]);
  });

  it("deletes a cell but always keeps at least one", () => {
    const first = conn.cells[0].id;
    const second = conn.addCell();
    conn.deleteCell(first);
    expect(ids()).toEqual([second]);
    // Deleting the last remaining cell replaces it with a fresh blank one.
    conn.deleteCell(second);
    expect(conn.cells.length).toBe(1);
    expect(conn.cells[0].id).not.toBe(second);
  });

  it("reorders cells within bounds and no-ops past the ends", () => {
    const a = conn.cells[0].id;
    const b = conn.addCell();
    const c = conn.addCell(); // [a, b, c]

    conn.moveCell(b, 1); // [a, c, b]
    expect(ids()).toEqual([a, c, b]);

    conn.moveCell(b, -1); // [a, b, c]
    expect(ids()).toEqual([a, b, c]);

    conn.moveCell(a, -1); // already first: no-op
    expect(ids()).toEqual([a, b, c]);

    conn.moveCell(c, 1); // already last: no-op
    expect(ids()).toEqual([a, b, c]);
  });
});

/** Minimal stand-in for the browser WebSocket: records every instance so a test
 *  can see whether a reconnect was attempted, and lets it fire the callbacks. */
class FakeWS {
  static instances: FakeWS[] = [];
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  /** Everything the store sent on this socket, decoded (see the watch tests). */
  sent: unknown[] = [];
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
}

describe("reconnect", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    vi.stubGlobal("WebSocket", FakeWS);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("retries after an unexpected close, after the backoff delay", () => {
    conn.status = "paused";
    conn.connect();
    expect(FakeWS.instances.length).toBe(1);
    FakeWS.instances[0].onopen?.(); // established -> backoff reset to the min

    // The socket drops (laptop sleep, network blip) while the debuggee is paused.
    FakeWS.instances[0].onclose?.();
    expect(conn.status).toBe("disconnected");

    // Nothing before the delay elapses, a fresh socket after it.
    vi.advanceTimersByTime(200);
    expect(FakeWS.instances.length).toBe(1);
    vi.advanceTimersByTime(100);
    expect(FakeWS.instances.length).toBe(2);
  });

  it("gives up once the debuggee has finished", () => {
    conn.status = "finished";
    conn.connect();
    FakeWS.instances[0].onclose?.();

    // The debuggee is gone for good: there is nothing to reconnect to.
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances.length).toBe(1);
    expect(conn.status).toBe("finished");
  });
});

describe("breakpoint notice", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    vi.stubGlobal("WebSocket", FakeWS);
    conn.notice = null;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    conn.notice = null;
  });

  const deliver = (msg: unknown) =>
    FakeWS.instances[0].onmessage?.({ data: JSON.stringify(msg) });

  it("surfaces a rejected breakpoint, then clears it on the next success", () => {
    conn.connect();
    FakeWS.instances[0].onopen?.();

    // A breakpoints reply carrying an error becomes a dismissable notice.
    deliver({
      type: "breakpoints",
      filename: "x.py",
      breakpoints: [],
      all_breakpoints: [],
      error: "No statement to break on at or after line 9.",
    });
    expect(conn.notice).toContain("No statement");

    // A subsequent successful set/clear clears the notice.
    deliver({
      type: "breakpoints",
      filename: "x.py",
      breakpoints: [{ line: 4 }],
      all_breakpoints: [{ filename: "x.py", line: 4 }],
    });
    expect(conn.notice).toBeNull();
  });
});

describe("exception pane", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    vi.stubGlobal("WebSocket", FakeWS);
    conn.exception = null;
    conn.postmortem = false;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    conn.exception = null;
    conn.postmortem = false;
  });

  const deliver = (msg: unknown) =>
    FakeWS.instances[0].onmessage?.({ data: JSON.stringify(msg) });

  const pausedBase = {
    filename: "x.py",
    lineno: 3,
    function: "f",
    locals: [],
    source: "",
    breakpoints: [],
    stack: [{ filename: "x.py", lineno: 3, function: "f" }],
    selected: 0,
    all_breakpoints: [],
  };

  it("captures a post-mortem exception and clears it on resume", () => {
    conn.connect();
    FakeWS.instances[0].onopen?.();

    deliver({
      type: "paused",
      ...pausedBase,
      postmortem: true,
      exception: {
        type: "ValueError",
        message: "kaboom",
        traceback: ["Traceback...\n", "ValueError: kaboom\n"],
      },
    });
    expect(conn.postmortem).toBe(true);
    expect(conn.exception?.type).toBe("ValueError");
    expect(conn.exception?.message).toBe("kaboom");

    // Resuming ends the pause: the crash belongs to that pause only.
    deliver({ type: "running" });
    expect(conn.exception).toBeNull();
    expect(conn.postmortem).toBe(false);
  });

  it("leaves the pane empty for an ordinary pause", () => {
    conn.connect();
    FakeWS.instances[0].onopen?.();
    deliver({ type: "paused", ...pausedBase });
    expect(conn.exception).toBeNull();
    expect(conn.postmortem).toBe(false);
  });
});

describe("watch expressions", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    vi.stubGlobal("WebSocket", FakeWS);
    localStorage.clear();
    conn.watches = [];
    conn.watchValues = [];
    conn.connect();
    FakeWS.instances[0].onopen?.();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    conn.watches = [];
    conn.watchValues = [];
  });

  const deliver = (msg: unknown) =>
    FakeWS.instances[0].onmessage?.({ data: JSON.stringify(msg) });

  /** The expression lists carried by every `set_watches` sent so far. */
  const sentLists = () =>
    FakeWS.instances
      .flatMap((ws) => ws.sent)
      .filter((m): m is { cmd: string; exprs: string[] } =>
        (m as { cmd?: string }).cmd === "set_watches",
      )
      .map((m) => m.exprs);

  it("sends the whole list on every edit", () => {
    conn.addWatch("df.shape");
    conn.addWatch("  total  "); // trimmed
    conn.addWatch("df.shape"); // duplicate: ignored
    conn.addWatch("   "); // blank: ignored
    expect(conn.watches).toEqual(["df.shape", "total"]);

    conn.setWatch(1, "total * 2");
    conn.removeWatch(0);
    expect(conn.watches).toEqual(["total * 2"]);

    expect(sentLists()).toEqual([
      ["df.shape"],
      ["df.shape", "total"],
      ["df.shape", "total * 2"],
      ["total * 2"],
    ]);
    // Persistence is the backend's job now: localStorage is scoped to the
    // server's port, which is random and therefore different on the next run.
    expect(localStorage.getItem("judb-watches")).toBeNull();
  });

  it("adopts the watch list the last run left behind", () => {
    deliver({ type: "notebook", cells: [], active: "", watches: ["df.shape"] });
    expect(conn.watches).toEqual(["df.shape"]);
    // The debugger holds only what it was last told, so hand it straight over.
    expect(sentLists().at(-1)).toEqual(["df.shape"]);
  });

  it("does not let a stored list overwrite one already on screen", () => {
    conn.addWatch("mine");
    deliver({ type: "notebook", cells: [], active: "", watches: ["theirs"] });
    expect(conn.watches).toEqual(["mine"]);
  });

  it("drops a row edited to blank or to a duplicate of another", () => {
    conn.addWatch("a");
    conn.addWatch("b");

    conn.setWatch(1, "   "); // emptied -> removed
    expect(conn.watches).toEqual(["a"]);

    conn.addWatch("b");
    conn.setWatch(1, "a"); // collapses onto the existing row
    expect(conn.watches).toEqual(["a"]);
  });

  it("matches values to rows by expression, not position", () => {
    conn.addWatch("a");
    conn.addWatch("b");
    deliver({
      type: "watches",
      watches: [
        { expr: "a", repr: { "text/plain": "1" }, summary: "int  1" },
        { expr: "b", error: "NameError: name 'b' is not defined" },
      ],
    });
    expect(conn.watchValue(0)?.summary).toBe("int  1");
    expect(conn.watchValue(1)?.error).toContain("NameError");

    // A just-edited row has no value yet — it must not show the old one.
    conn.setWatch(0, "a + 1");
    expect(conn.watchValue(0)).toBeUndefined();
  });

  it("hands the list to the backend again on reconnect", () => {
    conn.addWatch("df.shape");
    // A fresh socket (refresh / dropped connection / a new debuggee run) has a
    // backend that knows nothing about our watches.
    conn.connect();
    FakeWS.instances[1].onopen?.();
    expect(FakeWS.instances[1].sent).toEqual([
      { cmd: "set_watches", exprs: ["df.shape"] },
    ]);
  });
});

describe("source navigation", () => {
  const pausedIn = (filename: string) => ({
    type: "paused",
    filename,
    lineno: 7,
    function: "f",
    locals: [],
    source: "a = 1\nb = 2\n",
    breakpoints: [],
    stack: [{ filename, lineno: 7, function: "f" }],
    selected: 0,
    all_breakpoints: [],
  });

  beforeEach(() => {
    FakeWS.instances = [];
    vi.stubGlobal("WebSocket", FakeWS);
    conn.viewing = null;
    conn.markedLine = 0;
    conn.notice = null;
    conn.connect();
    FakeWS.instances[0].onopen?.();
    deliverTo0(pausedIn("/proj/frame.py"));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    conn.viewing = null;
    conn.markedLine = 0;
    conn.notice = null;
  });

  function deliverTo0(msg: unknown) {
    FakeWS.instances[0].onmessage?.({ data: JSON.stringify(msg) });
  }

  const sourceReply = (filename: string, extra: object = {}) => ({
    type: "source",
    filename,
    source: "def work():\n    return 1\n",
    breakpoints: [],
    ...extra,
  });

  it("shows an opened file and sends breakpoints to *that* file", () => {
    conn.openFile("/proj/other.py", 2);
    deliverTo0(sourceReply("/proj/other.py"));

    expect(conn.browsing).toBe(true);
    expect(conn.shownFilename).toBe("/proj/other.py");
    expect(conn.shownSource).toContain("def work");
    expect(conn.markedLine).toBe(2);

    // The gutter acts on the file on screen, not on the paused frame's.
    conn.toggleBreak(2);
    expect(FakeWS.instances[0].sent.at(-1)).toEqual({
      cmd: "set_break",
      filename: "/proj/other.py",
      line: 2,
    });

    // ...and that file's `breakpoints` reply lands in the browsed view.
    deliverTo0({
      type: "breakpoints",
      filename: "/proj/other.py",
      breakpoints: [{ line: 2 }],
      all_breakpoints: [{ filename: "/proj/other.py", line: 2 }],
    });
    expect(conn.shownBreakpoints).toEqual([{ line: 2 }]);
    expect(conn.breakpoints).toEqual([]); // the frame's file is untouched
  });

  it("treats opening the frame's own file as a scroll, not a detour", () => {
    conn.openFile("/proj/frame.py", 2);
    deliverTo0(sourceReply("/proj/frame.py"));
    expect(conn.browsing).toBe(false);
    expect(conn.markedLine).toBe(2);
  });

  it("goes back to the frame, and lands there again on the next pause", () => {
    conn.openFile("/proj/other.py", 2);
    deliverTo0(sourceReply("/proj/other.py"));
    conn.backToFrame();
    expect(conn.browsing).toBe(false);
    expect(conn.shownFilename).toBe("/proj/frame.py");

    // A new pause always wins over browsing: where the debuggee *is* outranks
    // what you were reading.
    conn.openFile("/proj/other.py");
    deliverTo0(sourceReply("/proj/other.py"));
    expect(conn.browsing).toBe(true);
    deliverTo0(pausedIn("/proj/frame.py"));
    expect(conn.browsing).toBe(false);
    expect(conn.markedLine).toBe(0);
  });

  it("reports an unreadable path and stays where it is", () => {
    conn.openFile("/nope.py", 3);
    deliverTo0(sourceReply("/nope.py", { source: "", error: "Cannot read /nope.py" }));
    expect(conn.notice).toContain("Cannot read");
    expect(conn.browsing).toBe(false);
    expect(conn.shownFilename).toBe("/proj/frame.py");
  });

  it("offers the files it already knows as open suggestions", () => {
    deliverTo0({
      ...pausedIn("/proj/frame.py"),
      stack: [
        { filename: "/proj/main.py", lineno: 2, function: "main" },
        { filename: "/proj/frame.py", lineno: 7, function: "f" },
      ],
      all_breakpoints: [{ filename: "/proj/lib.py", line: 4 }],
    });
    expect(conn.knownFiles).toEqual(["/proj/frame.py", "/proj/lib.py", "/proj/main.py"]);
  });
});

// Everything below gets a *fresh* store per test: "once a session" state (the
// setup cells, the pending save) must not leak from one test into the next.
function session() {
  FakeWS.instances = [];
  const conn = new Connection();
  conn.connect();
  const ws = FakeWS.instances[0];
  ws.onopen?.();
  const deliver = (msg: unknown) => ws.onmessage?.({ data: JSON.stringify(msg) });
  const sent = (cmd: string) =>
    ws.sent.filter((m): m is Record<string, unknown> => (m as { cmd?: string }).cmd === cmd);
  const paused = (location: string) =>
    deliver({
      type: "paused",
      filename: "demo.py",
      lineno: 3,
      function: "clean",
      locals: [],
      source: "",
      breakpoints: [],
      stack: [{ filename: "demo.py", lineno: 3, function: "clean" }],
      selected: 0,
      all_breakpoints: [],
      location,
    });
  const library = (user: SavedCell[], extra: object = {}) =>
    deliver({
      type: "library",
      user,
      project: [],
      paths: { user: "/u/cells.py", project: "/p/.judb/cells.py" },
      ...extra,
    });
  return { conn, ws, deliver, sent, paused, library };
}

const saved = (code: string, at = "", tags: string[] = [], name = ""): SavedCell => ({
  code,
  name,
  at,
  tags,
});

describe("the saved notebook", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWS);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("groups the restored cells into one tab per location key", () => {
    const { conn, deliver } = session();
    deliver({
      type: "notebook",
      cells: [
        saved("scale * 2"),
        saved("df.plot()", "demo.py::clean", [], "the plot"),
        saved("df.head()", "demo.py::clean"),
      ],
      active: "demo.py::clean",
      watches: [],
    });
    expect(conn.tabs.map((t) => t.at)).toEqual(["", "demo.py::clean"]);
    expect(conn.tabs[1].cells.map((c) => c.code)).toEqual(["df.plot()", "df.head()"]);
    // The tab that was in front last time is in front again.
    expect(conn.active).toBe(1);
    expect(conn.cells[0].name).toBe("the plot");
  });

  it("never overwrites cells that are already on screen", () => {
    const { conn, deliver } = session();
    conn.cells[0].code = "live work";
    deliver({ type: "notebook", cells: [saved("from disk")], active: "", watches: [] });
    expect(conn.cells.map((c) => c.code)).toEqual(["live work"]);
  });

  it("batches edits into one save of the whole notebook, flat", () => {
    const { conn, sent } = session();
    conn.editCell(conn.cells[0].id, "df");
    conn.editCell(conn.cells[0].id, "df.head()");
    expect(sent("save_notebook")).toEqual([]); // not on every keystroke
    vi.runAllTimers();
    expect(sent("save_notebook")).toEqual([
      { cmd: "save_notebook", cells: [saved("df.head()")], active: "" },
    ]);
  });

  it("saves within the delay even while the typing never stops", () => {
    // A timer that restarts on every keystroke saves nothing until the user
    // pauses — and loses all of it if the program ends first.
    const { conn, sent } = session();
    for (let i = 1; i <= 10; i++) {
      conn.editCell(conn.cells[0].id, "x".repeat(i));
      vi.advanceTimersByTime(100);
    }
    expect(sent("save_notebook").length).toBeGreaterThanOrEqual(1);
  });

  it.each(["continue", "next", "step", "return", "quit"] as const)(
    "writes unsaved cells before `%s` — the program may never pause again",
    (cmd) => {
      const { conn, ws } = session();
      conn.editCell(conn.cells[0].id, "the cell that found the bug");
      conn.send({ cmd });
      // No timer has fired: the save went out first, on the same socket, so
      // the server has written it before the debuggee is told to move.
      expect(ws.sent.map((m) => (m as { cmd: string }).cmd)).toEqual(["save_notebook", cmd]);
      expect((ws.sent[0] as { cells: SavedCell[] }).cells[0].code).toBe("the cell that found the bug");
    },
  );

  it("writes the notebook before running a cell, which may end the process", () => {
    const { conn, ws, paused } = session();
    paused("demo.py::clean");
    conn.runCell(conn.cells[0].id, "import sys; sys.exit()");
    expect(ws.sent.map((m) => (m as { cmd: string }).cmd)).toEqual(["save_notebook", "execute_cell"]);
  });

  it("has nothing to flush when nothing changed", () => {
    const { conn, ws } = session();
    conn.send({ cmd: "continue" });
    conn.flush();
    expect(ws.sent).toEqual([{ cmd: "continue" }]);
  });

  it("keeps setup cells out of the saved notebook", () => {
    const { conn, sent, library } = session();
    library([saved("import numpy as np", "", ["setup"], "imports")]);
    expect(conn.cells[0].setup).toBe(true);
    conn.editCell(conn.cells[1].id, "mine");
    vi.runAllTimers();
    expect(sent("save_notebook").at(-1)?.cells).toEqual([saved("mine")]);
  });
});

describe("tabs follow the frame", () => {
  beforeEach(() => vi.stubGlobal("WebSocket", FakeWS));
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["", "a/b.py::f", true], // no key: always available
    ["a/b.py", "a/b.py::f", true], // file key: anywhere in the file
    ["a/b.py", "a/b.py", true], // …including its module frame
    ["a/b.py::f", "a/b.py::f", true], // function key: only itself
    ["a/b.py::f", "a/b.py::g", false],
    ["a/b.py::f", "a/b.py", false],
    ["a/b.py", "a/bc.py::f", false], // not a prefix match on the name
  ])("the location ladder: %j at %j → %s", (at, location, expected) => {
    expect(matchesLocation(at, location)).toBe(expected);
  });

  it("follows the frame into a pinned tab, but not out of a focused editor", () => {
    const { conn, paused } = session();
    conn.cells[0].code = "df.plot()";
    conn.pinCell(conn.cells[0].id, "demo.py::clean");
    expect(conn.tabs.map((t) => t.at)).toEqual(["", "demo.py::clean"]);
    expect(conn.active).toBe(1);

    conn.selectTab(0);
    paused("demo.py::clean");
    expect(conn.active).toBe(1); // the frame brought its tab to the front

    // …unless the user is typing: badge it and leave the console alone.
    conn.selectTab(0);
    conn.editing = true;
    paused("demo.py::clean");
    expect(conn.active).toBe(0);
    expect(conn.pendingTab).toBe(1);
  });

  it("stays put when the frame has no tab of its own", () => {
    const { conn, paused } = session();
    conn.cells[0].code = "df.plot()";
    conn.pinCell(conn.cells[0].id, "demo.py::clean");
    paused("other.py::somewhere_else");
    expect(conn.active).toBe(1);
    expect(conn.pendingTab).toBe(-1);
  });

  it("brings a file-pinned tab forward anywhere in that file", () => {
    const { conn, paused } = session();
    conn.cells[0].code = "FACTOR";
    conn.pinCell(conn.cells[0].id, "etl/clean.py");
    conn.selectTab(0);
    paused("etl/clean.py::helper");
    expect(conn.tabs[conn.active].at).toBe("etl/clean.py");
  });

  it("prefers the function's own tab to its file's", () => {
    const { conn, paused } = session();
    conn.cells[0].code = "FACTOR";
    conn.pinCell(conn.cells[0].id, "etl/clean.py");
    conn.selectTab(0);
    conn.cells[0].code = "total";
    conn.pinCell(conn.cells[0].id, "etl/clean.py::run");
    conn.selectTab(0);
    paused("etl/clean.py::run");
    expect(conn.tabs[conn.active].at).toBe("etl/clean.py::run");
    paused("etl/clean.py::helper");
    expect(conn.tabs[conn.active].at).toBe("etl/clean.py");
  });

  it("never treats Scratch as a match to switch to", () => {
    const { conn, paused } = session();
    conn.cells[0].code = "total";
    conn.pinCell(conn.cells[0].id, "etl/clean.py::run");
    paused("main.py::main"); // Scratch "matches everywhere", but is not *pinned* here
    expect(conn.tabs[conn.active].at).toBe("etl/clean.py::run");
  });
});

describe("the cell libraries", () => {
  beforeEach(() => vi.stubGlobal("WebSocket", FakeWS));
  afterEach(() => vi.unstubAllGlobals());

  const mine = [saved("import math", "", ["setup"], "imports"), saved("dir()", "", [], "everywhere")];

  it("saving a tab only ever adds: it sends that tab's cells, not the library", () => {
    // The bug this pins down: saving Scratch sent the *whole* library back with
    // every un-keyed cell replaced — which deleted the user's setup cells.
    const { conn, sent, library } = session();
    library(mine);
    conn.cells.at(-1)!.code = "value + 1";
    conn.saveTabTo("user");
    expect(sent("library_add")).toEqual([
      { cmd: "library_add", scope: "user", cells: [saved("value + 1")] },
    ]);
  });

  it("saves a pinned tab under its location key, to the project", () => {
    const { conn, sent } = session();
    conn.cells[0].code = "total";
    conn.pinCell(conn.cells[0].id, "etl/clean.py::run");
    conn.saveTabTo("project");
    expect(sent("library_add")).toEqual([
      { cmd: "library_add", scope: "project", cells: [saved("total", "etl/clean.py::run")] },
    ]);
  });

  it("sends nothing for a tab with nothing in it", () => {
    const { conn, ws } = session();
    conn.saveTabTo("user");
    expect(ws.sent).toEqual([]);
  });

  it("removes and re-keys by naming the cell, never by resending the file", () => {
    const { conn, sent, library, paused } = session();
    library(mine);
    paused("etl/clean.py::run");
    conn.removeFromLibrary("user", mine[1]);
    conn.rekeyLibraryCell("user", mine[1]);
    expect(sent("library_remove")).toEqual([{ cmd: "library_remove", scope: "user", cell: mine[1] }]);
    expect(sent("library_rekey")).toEqual([
      { cmd: "library_rekey", scope: "user", cell: mine[1], at: "etl/clean.py::run" },
    ]);
  });

  it("runs setup cells once a session, not once per library message", () => {
    const { conn, sent, library, paused } = session();
    library(mine);
    paused("demo.py::clean");
    expect(sent("execute_cell").map((m) => m.code)).toEqual(["import math"]);

    // The user deletes the setup cell; then any library edit echoes `library`.
    conn.deleteCell(conn.cells[0].id);
    library(mine);
    expect(conn.cells.some((c) => c.setup)).toBe(false); // it stays deleted…
    expect(sent("execute_cell")).toHaveLength(1); // …and does not run again
  });

  it("shows a save that failed instead of looking like one that worked", () => {
    const { conn, library } = session();
    library([], { error: "could not save the cell library: read-only file system" });
    expect(conn.notice).toContain("could not save");
  });

  it("inserts a library cell into the tab in front", () => {
    const { conn } = session();
    conn.insertFromLibrary(mine[1]);
    expect(conn.cells.at(-1)?.code).toBe("dir()");
  });
});
