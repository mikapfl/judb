// Responsive layout: how the panes are arranged at the current window width,
// and which of the secondary panes are on screen at all.
//
// One rule drives the top half: **the window never squeezes the Source pane
// below PEP 8's 80 columns**. Debugging is reading code, and code that soft-wraps
// at column 60 is code you can't read. So the console sits *beside* the source
// only while both can have a usable width; below that it folds *under* it, and
// the source gets the full width. The floor is measured in real characters of
// the editor's own font (`measureCharWidth`), not guessed in pixels, so it holds
// up when the user's monospace face or `--font-size` differs from ours.
//
// The floor is a floor on *automatic* layout, not a veto on the user: drag the
// splitter narrower than 80 columns and that is honoured, and honoured from then
// on (`reconcileSource`), because someone who wants a sliver of source and a
// wide console is entitled to it. Drag back to the floor and the automatic
// protection re-arms.
//
// The bottom half is a grid that reflows: every secondary pane side by side
// while each still gets `MIN_SECONDARY_PX`, then two rows, then three. Panes the
// user has closed are simply not in the grid — which is also what makes narrow
// windows workable, since closing three panes is another way to buy width.
//
// Everything here is pure layout: no protocol, no persistence beyond the closed
// list in localStorage (like the theme and the watch list — a pure-UI preference
// the backend deliberately has no opinion about; see config.py's docstring).

/** The secondary panes, in the order they are laid out. */
export type PaneId = "breakpoints" | "stack" | "variables" | "watch" | "exception";

export const SECONDARY_PANES: { id: PaneId; title: string }[] = [
  { id: "breakpoints", title: "Breakpoints" },
  { id: "stack", title: "Call stack" },
  // Variables and Watch are adjacent so they share a row for as long as the
  // grid can manage it: both answer "what is this value right now".
  { id: "variables", title: "Variables" },
  { id: "watch", title: "Watch" },
  { id: "exception", title: "Exception" },
];

/** PEP 8's line length — the whole point of the width floor. */
export const SOURCE_COLS = 80;

/** Columns the source editor spends on chrome before column 1 of the code: the
 *  breakpoint gutter (~1.1em) plus room for four-digit line numbers. */
const GUTTER_COLS = 7;

/** Padding/scrollbar slack, in px, on top of the measured character grid. */
const SOURCE_SLACK_PX = 12;

/** Below this the console is too cramped to be a notebook; fold it under. */
export const MIN_CONSOLE_PX = 340;

/** A secondary pane narrower than this shows nothing useful — reflow instead. */
export const MIN_SECONDARY_PX = 260;

/** Splitter thickness, from the `judb-split` rules in App.svelte. */
const SPLITTER_PX = 5;

/** Used until a real measurement is possible (jsdom has no canvas): ~0.6em at
 *  the default 13px, which is typical for a monospace face. */
export const FALLBACK_CHAR_PX = 7.8;

const STORAGE_KEY = "judb-panes";

/** Width one character of the source editor's font occupies, in px. */
export function measureCharWidth(): number {
  try {
    const root = getComputedStyle(document.documentElement);
    const size = root.getPropertyValue("--font-size").trim() || "13px";
    const family = root.getPropertyValue("--font-mono").trim() || "monospace";
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) return FALLBACK_CHAR_PX;
    ctx.font = `${size} ${family}`;
    // Measure a run of characters rather than one, so sub-pixel advance widths
    // average out instead of rounding us under the floor.
    const width = ctx.measureText("0".repeat(SOURCE_COLS)).width / SOURCE_COLS;
    return width > 0 ? width : FALLBACK_CHAR_PX;
  } catch {
    return FALLBACK_CHAR_PX;
  }
}

/** Narrowest the source pane may be: 80 columns of code plus its gutters. */
export function sourceMinPx(charPx: number): number {
  return (SOURCE_COLS + GUTTER_COLS) * charPx + SOURCE_SLACK_PX;
}

/** Does the console still fit *beside* an 80-column source at this width? */
export function consoleFitsBeside(width: number, charPx: number): boolean {
  // Width 0 means "not measured yet" — assume roomy, so the first paint doesn't
  // flash the stacked layout on a perfectly wide window.
  if (width <= 0) return true;
  return width >= sourceMinPx(charPx) + MIN_CONSOLE_PX + SPLITTER_PX;
}

/** How many rows `count` secondary panes need at this width (1, 2 or 3). */
export function secondaryRows(count: number, width: number): number {
  if (count <= 1) return 1;
  if (width <= 0) return 1;
  const perRow = (rows: number) => Math.ceil(count / rows);
  const fits = (rows: number) =>
    (width - (perRow(rows) - 1) * SPLITTER_PX) / perRow(rows) >= MIN_SECONDARY_PX;

  for (const rows of [1, 2, 3]) if (fits(rows)) return rows;
  // Narrower than three rows can serve: take the fewest rows that still reach
  // the narrowest row we can build. Four panes over three rows would be 2-1-1 —
  // an extra row that makes nothing wider than the 2-2 two-row version does.
  const best = Math.min(...[1, 2, 3].map(perRow));
  return [1, 2, 3].find((rows) => perRow(rows) === best)!;
}

/** Split `items` into `rows` rows, as evenly as possible, fuller rows first. */
export function distribute<T>(items: T[], rows: number): T[][] {
  const out: T[][] = [];
  let rest = items;
  for (let left = rows; left > 0; left--) {
    const take = Math.ceil(rest.length / left);
    out.push(rest.slice(0, take));
    rest = rest.slice(take);
  }
  return out.filter((row) => row.length > 0);
}

/** A px width as a percentage of the container, clamped to something usable. */
export function pctOf(px: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(90, (px / total) * 100));
}

/** How narrow the splitter may drag the source or the console, in percent.
 *  Not the 80-column floor — this is only "still a pane, not a sliver". */
export const MIN_PANE_PCT = 10;

/** Widest the automatic floor will push the source, leaving the console alive. */
const MAX_SOURCE_PCT = 85;

/** Percentage-point slack: a size the clamp itself just wrote must not read
 *  back as the user having chosen something narrower. */
const PCT_EPSILON = 0.5;

/** The source pane's share of the working area, and whether the user has
 *  deliberately dragged it below the 80-column floor. */
export interface SourceSplit {
  size: number;
  userNarrowed: boolean;
}

/**
 * Reconcile the source pane's share after something moved it.
 *
 * The whole point is *which* something. A window resize must not silently
 * squeeze the editor below the floor, so it gets pushed back up; a drag is the
 * user saying what they want, so it stands — and it keeps standing through
 * later resizes, until they drag back to the floor themselves.
 *
 * While the console is folded under, `size` is a share of *height* and the
 * column floor means nothing, so nothing is decided (and no drag down there is
 * mistaken for a width preference).
 */
export function reconcileSource(
  current: SourceSplit,
  minPct: number,
  windowChanged: boolean,
  stacked: boolean,
): SourceSplit {
  if (stacked) return current;
  if (!windowChanged) {
    return { ...current, userNarrowed: current.size < minPct - PCT_EPSILON };
  }
  if (current.userNarrowed || current.size >= minPct) return current;
  return { size: Math.min(minPct, MAX_SOURCE_PCT), userNarrowed: false };
}

function loadClosed(): PaneId[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const known = new Set<string>(SECONDARY_PANES.map((p) => p.id));
    // Storing the *closed* set (rather than the open one) means a pane added in
    // a later version shows up for existing users instead of staying invisible.
    return parsed.filter((id): id is PaneId => typeof id === "string" && known.has(id));
  } catch {
    return [];
  }
}

class Layout {
  /** Width of the pane area, in px (0 until the first measurement). */
  width = $state(0);
  /** Width of one character in the editor font, in px. */
  charPx = $state(FALLBACK_CHAR_PX);

  #closed = $state<PaneId[]>(loadClosed());

  /** Follow an element's width. Use as an attachment: `{@attach layout.observe}`. */
  observe = (el: HTMLElement): (() => void) => {
    this.charPx = measureCharWidth();
    this.width = el.clientWidth;
    if (typeof ResizeObserver !== "function") return () => {};
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) this.width = entry.contentRect.width;
    });
    ro.observe(el);
    return () => ro.disconnect();
  };

  /** Narrowest the source pane may be, in px and as a percentage. */
  get sourceMinPx(): number {
    return sourceMinPx(this.charPx);
  }
  get sourceMinPct(): number {
    return pctOf(this.sourceMinPx, this.width);
  }

  /** True when the console has folded under the source instead of beside it. */
  get stackConsole(): boolean {
    return !consoleFitsBeside(this.width, this.charPx);
  }

  /** The secondary panes the user has left open, in layout order. */
  get visible(): PaneId[] {
    return SECONDARY_PANES.map((p) => p.id).filter((id) => !this.#closed.includes(id));
  }

  /** The visible secondary panes, grouped into the rows they are laid out in. */
  get rows(): PaneId[][] {
    const visible = this.visible;
    if (visible.length === 0) return [];
    return distribute(visible, secondaryRows(visible.length, this.width));
  }

  /** Panes the user has closed, in layout order (what the Panes menu re-opens). */
  get closed(): PaneId[] {
    return SECONDARY_PANES.map((p) => p.id).filter((id) => this.#closed.includes(id));
  }

  isOpen(id: PaneId): boolean {
    return !this.#closed.includes(id);
  }

  setOpen(id: PaneId, open: boolean): void {
    this.#closed = open
      ? this.#closed.filter((other) => other !== id)
      : this.#closed.includes(id)
        ? this.#closed
        : [...this.#closed, id];
    this.#persist();
  }

  toggle(id: PaneId): void {
    this.setOpen(id, !this.isOpen(id));
  }

  showAll(): void {
    this.#closed = [];
    this.#persist();
  }

  #persist(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.#closed));
    } catch {
      // best-effort persistence, exactly as in theme.svelte.ts
    }
  }
}

export const layout = new Layout();
