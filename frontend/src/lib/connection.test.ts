import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conn, type Cell } from "./connection.svelte";

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

  it("sends the whole list on every edit and persists it", () => {
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
    // Persisted, so a refresh (and the next run of the same debuggee) keeps it.
    expect(JSON.parse(localStorage.getItem("judb-watches") ?? "[]")).toEqual([
      "total * 2",
    ]);
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
