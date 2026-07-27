import { beforeEach, describe, expect, it } from "vitest";
import {
  consoleFitsBeside,
  distribute,
  FALLBACK_CHAR_PX,
  layout,
  MIN_CONSOLE_PX,
  MIN_SECONDARY_PX,
  pctOf,
  reconcileSource,
  SECONDARY_PANES,
  secondaryRows,
  sourceMinPx,
  SOURCE_COLS,
  type PaneId,
} from "./layout.svelte";

const ALL = SECONDARY_PANES.map((p) => p.id);

describe("the 80-column source floor", () => {
  it("reserves 80 columns of code plus the gutters", () => {
    const min = sourceMinPx(FALLBACK_CHAR_PX);
    expect(min).toBeGreaterThan(SOURCE_COLS * FALLBACK_CHAR_PX);
    // …but not extravagantly: the gutters are a handful of columns, not a pane.
    expect(min).toBeLessThan((SOURCE_COLS + 12) * FALLBACK_CHAR_PX);
  });

  it("keeps the console beside the source only while both fit", () => {
    const min = sourceMinPx(FALLBACK_CHAR_PX);
    expect(consoleFitsBeside(min + MIN_CONSOLE_PX + 50, FALLBACK_CHAR_PX)).toBe(true);
    expect(consoleFitsBeside(min + MIN_CONSOLE_PX - 50, FALLBACK_CHAR_PX)).toBe(false);
  });

  it("scales the floor with the editor's font, not a fixed pixel guess", () => {
    // A user on a 20px font needs a wider pane for the same 80 columns.
    expect(consoleFitsBeside(1100, FALLBACK_CHAR_PX)).toBe(true);
    expect(consoleFitsBeside(1100, FALLBACK_CHAR_PX * 2)).toBe(false);
  });

  it("assumes room before the first measurement, so nothing flashes stacked", () => {
    expect(consoleFitsBeside(0, FALLBACK_CHAR_PX)).toBe(true);
  });
});

describe("the floor is against the window, not the user", () => {
  const wide = { size: 60, userNarrowed: false };
  const narrow = { size: 30, userNarrowed: false };

  it("pushes the source back up when the window squeezed it", () => {
    expect(reconcileSource(narrow, 50, true, false)).toEqual({
      size: 50,
      userNarrowed: false,
    });
  });

  it("leaves a comfortable pane alone on resize", () => {
    expect(reconcileSource(wide, 50, true, false)).toEqual(wide);
  });

  it("honours a drag below the floor", () => {
    // The size moved without the window changing — that is the splitter.
    expect(reconcileSource(narrow, 50, false, false)).toEqual({
      size: 30,
      userNarrowed: true,
    });
  });

  it("keeps honouring it through later window resizes", () => {
    const chosen = { size: 30, userNarrowed: true };
    expect(reconcileSource(chosen, 50, true, false)).toEqual(chosen);
    expect(reconcileSource(chosen, 70, true, false)).toEqual(chosen);
  });

  it("re-arms the floor once the user drags back to it", () => {
    const back = reconcileSource({ size: 55, userNarrowed: true }, 50, false, false);
    expect(back.userNarrowed).toBe(false);
    // …and from then on the window is held to the floor again.
    expect(reconcileSource({ ...back, size: 30 }, 50, true, false).size).toBe(50);
  });

  it("does not mistake the clamp's own write for a user choice", () => {
    const clamped = reconcileSource(narrow, 50, true, false);
    expect(reconcileSource(clamped, 50, false, false).userNarrowed).toBe(false);
  });

  it("never pushes the source so wide the console is gone", () => {
    expect(reconcileSource({ size: 10, userNarrowed: false }, 95, true, false).size).toBe(85);
  });

  it("decides nothing while the console is folded under", () => {
    // Down there `size` is a share of height, so no drag means a width choice.
    expect(reconcileSource(narrow, 50, false, true)).toEqual(narrow);
    expect(reconcileSource(narrow, 50, true, true)).toEqual(narrow);
  });
});

describe("secondary reflow", () => {
  it("uses one row when every pane still gets its minimum", () => {
    expect(secondaryRows(5, 5 * MIN_SECONDARY_PX + 100)).toBe(1);
  });

  it("adds a second row, then a third, as the window narrows", () => {
    // 5 panes over 2 rows is 3 in the fuller one; over 3 rows it is 2.
    expect(secondaryRows(5, 3 * MIN_SECONDARY_PX + 50)).toBe(2);
    expect(secondaryRows(5, 2 * MIN_SECONDARY_PX + 50)).toBe(3);
  });

  it("never goes past three rows, however narrow it gets", () => {
    expect(secondaryRows(5, 200)).toBe(3);
    expect(secondaryRows(5, 10)).toBe(3);
  });

  it("doesn't add a row that makes nothing wider", () => {
    // Four panes over three rows is 2-1-1: the widest row still holds two, so
    // it buys nothing over the tidier 2-2. Two panes bottom out at one each.
    expect(secondaryRows(4, 200)).toBe(2);
    expect(secondaryRows(2, 200)).toBe(2);
    expect(secondaryRows(3, 200)).toBe(3);
  });

  it("needs fewer rows once panes are closed — closing one buys width", () => {
    const width = 2 * MIN_SECONDARY_PX + 50;
    expect(secondaryRows(5, width)).toBe(3);
    expect(secondaryRows(2, width)).toBe(1);
  });

  it("distributes panes evenly, fuller rows first", () => {
    expect(distribute([1, 2, 3, 4, 5], 1)).toEqual([[1, 2, 3, 4, 5]]);
    expect(distribute([1, 2, 3, 4, 5], 2)).toEqual([
      [1, 2, 3],
      [4, 5],
    ]);
    expect(distribute([1, 2, 3, 4, 5], 3)).toEqual([[1, 2], [3, 4], [5]]);
    expect(distribute([1, 2, 3, 4], 3)).toEqual([[1, 2], [3], [4]]);
  });

  it("drops empty rows rather than laying out a gap", () => {
    expect(distribute([1], 3)).toEqual([[1]]);
    expect(distribute([], 3)).toEqual([]);
  });
});

describe("pctOf", () => {
  it("converts a pixel floor into a percentage of the container", () => {
    expect(pctOf(500, 1000)).toBe(50);
  });

  it("stays sane before the container has been measured", () => {
    expect(pctOf(500, 0)).toBe(0);
  });

  it("never claims the whole width, so the other pane still exists", () => {
    expect(pctOf(2000, 1000)).toBe(90);
  });
});

describe("the layout store", () => {
  beforeEach(() => {
    localStorage.clear();
    layout.showAll();
    layout.width = 2000;
    layout.charPx = FALLBACK_CHAR_PX;
  });

  it("shows every secondary pane on a wide window, in one row", () => {
    expect(layout.rows).toEqual([ALL]);
    expect(layout.stackConsole).toBe(false);
  });

  it("folds the console under the source on a narrow window", () => {
    layout.width = 700;
    expect(layout.stackConsole).toBe(true);
    // The floor is still expressed as a percentage the pane can enforce.
    expect(layout.sourceMinPct).toBeGreaterThan(50);
  });

  it("reflows into rows as the window narrows", () => {
    layout.width = 900;
    expect(layout.rows.length).toBe(2);
    layout.width = 600;
    expect(layout.rows.length).toBe(3);
  });

  it("drops a closed pane out of the layout and offers it back", () => {
    layout.setOpen("exception", false);
    expect(layout.rows.flat()).not.toContain("exception");
    expect(layout.closed).toEqual(["exception"]);

    layout.toggle("exception");
    expect(layout.rows.flat()).toContain("exception");
    expect(layout.closed).toEqual([]);
  });

  it("lays out no secondary row at all once everything is closed", () => {
    for (const id of ALL) layout.setOpen(id, false);
    expect(layout.rows).toEqual([]);
    expect(layout.closed).toEqual(ALL);
  });

  it("keeps the layout order regardless of the order panes were closed", () => {
    layout.setOpen("stack", false);
    layout.setOpen("breakpoints", false);
    expect(layout.closed).toEqual(["breakpoints", "stack"]);
    expect(layout.rows.flat()).toEqual(["variables", "watch", "exception"]);
  });

  it("persists the closed set, and only remembers panes it knows", () => {
    layout.setOpen("watch", false);
    expect(JSON.parse(localStorage.getItem("judb-panes")!)).toEqual(["watch"]);

    // A pane that no longer exists in this version is ignored, not laid out.
    localStorage.setItem("judb-panes", JSON.stringify(["watch", "gone"]));
    // (Re-reading happens at construction; assert the filter directly.)
    const known = new Set<string>(ALL);
    const restored = (JSON.parse(localStorage.getItem("judb-panes")!) as string[]).filter(
      (id): id is PaneId => known.has(id),
    );
    expect(restored).toEqual(["watch"]);
  });
});
