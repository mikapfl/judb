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
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(): void {}
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
