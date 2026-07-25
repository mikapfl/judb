// Shared CodeMirror 6 building blocks for the source pane (read-only, current
// line) and the console cells (editable). Framework-agnostic: panes mount an
// EditorView into a node. See PHASE2_STACK.md §3.

import { EditorState, StateEffect, StateField } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  GutterMarker,
  gutter,
  keymap,
  lineNumbers,
  type DecorationSet,
} from "@codemirror/view";
import { syntaxHighlighting, HighlightStyle } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { python } from "@codemirror/lang-python";
import {
  autocompletion,
  startCompletion,
  type CompletionSource,
} from "@codemirror/autocomplete";
import type { Breakpoint } from "../protocol";

/** Editor chrome, driven entirely by tokens.css custom properties so the source
 *  and console editors follow the light/dark theme without being rebuilt.
 *  Selection + caret are styled explicitly (rather than leaning on CodeMirror's
 *  built-in `dark` base) so both themes look right. */
export const judbTheme = EditorView.theme({
  "&": { color: "var(--fg)", backgroundColor: "transparent", height: "100%" },
  ".cm-content": { fontFamily: "var(--font-mono)", caretColor: "var(--fg)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--fg)" },
  // No `drawSelection` extension is loaded, so selection uses the native layer.
  "& .cm-line::selection, & .cm-line ::selection": {
    backgroundColor: "var(--cm-selection)",
  },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "var(--cm-selection)",
  },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "var(--fg-faint)",
    border: "none",
  },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-current-line": { backgroundColor: "var(--accent-bg)" },
  ".cm-scroller": { fontFamily: "var(--font-mono)" },
  "&.cm-focused": { outline: "none" },
  // Clickable gutter: a red dot marks a set breakpoint (a diamond if it carries
  // a condition/temporary/ignore option); every other line has a transparent
  // slot that reveals a faint dot on hover, inviting a click.
  ".cm-breakpoint-gutter": { width: "1.1em", cursor: "pointer" },
  ".cm-breakpoint-gutter .cm-gutterElement": { paddingLeft: "0.15em" },
  ".cm-breakpoint": { color: "var(--err-fg, #e06c75)" },
  ".cm-breakpoint-cond": { color: "var(--warn-fg, #d19a66)" },
  ".cm-breakpoint-slot": { color: "transparent" },
  ".cm-breakpoint-gutter .cm-gutterElement:hover .cm-breakpoint-slot": {
    color: "var(--err-fg, #e06c75)",
    opacity: "0.4",
  },
  // Tab-completion popup. Unthemed it inherits CodeMirror's light base theme
  // (white background, near-white text) and every unselected row is unreadable
  // in dark mode — so paint it from tokens like the rest of the chrome.
  ".cm-tooltip.cm-tooltip-autocomplete": {
    backgroundColor: "var(--bg-raised)",
    border: "1px solid var(--border-strong)",
    borderRadius: "4px",
  },
  ".cm-tooltip-autocomplete > ul": { fontFamily: "var(--font-mono)" },
  ".cm-tooltip-autocomplete > ul > li": { color: "var(--fg)" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "var(--accent-bg)",
    color: "var(--fg)",
  },
  ".cm-completionMatchedText": {
    color: "var(--accent)",
    textDecoration: "none",
    fontWeight: "bold",
  },
  ".cm-completionDetail": { color: "var(--fg-dim)" },
});

/** Python syntax palette, coloured via CSS variables (tokens.css) so it recolours
 *  on a theme switch. Replaces CodeMirror's fixed `defaultHighlightStyle`.
 *
 *  This is the app's *only* definition of "what colour is a keyword". Anything
 *  else that shows Python — the source pane, console cells, and the Exception
 *  pane's traceback (see highlight.ts) — highlights through this object, so a
 *  theme change is a pure CSS-variable swap with no second palette to keep in
 *  step. Nothing in the backend has a colour opinion. */
export const judbHighlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword], color: "var(--tok-keyword)" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "var(--tok-string)" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--tok-number)" },
  {
    tag: [t.comment, t.lineComment, t.blockComment],
    color: "var(--tok-comment)",
    fontStyle: "italic",
  },
  {
    tag: [t.function(t.variableName), t.function(t.propertyName)],
    color: "var(--tok-fn)",
  },
  { tag: t.definition(t.variableName), color: "var(--tok-def)" },
  { tag: [t.typeName, t.className, t.namespace], color: "var(--tok-type)" },
  { tag: [t.operator, t.operatorKeyword, t.derefOperator], color: "var(--tok-operator)" },
  { tag: [t.self, t.standard(t.variableName)], color: "var(--tok-builtin)" },
  { tag: [t.variableName, t.propertyName], color: "var(--tok-variable)" },
]);

/** The Python parser, exposed for highlighting standalone snippets (highlight.ts)
 *  without standing up an `EditorView`. */
export const pythonParser = python().language.parser;

// --- current-line highlight (source pane) -------------------------------

/** Set the 1-based current line, or 0 to clear. */
export const setCurrentLine = StateEffect.define<number>();

const currentLineDeco = Decoration.line({ class: "cm-current-line" });

export const currentLineField = StateField.define<DecorationSet>({
  create() {
    return Decoration.none;
  },
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setCurrentLine)) {
        const n = e.value;
        if (n < 1 || n > tr.state.doc.lines) return Decoration.none;
        const line = tr.state.doc.line(n);
        return Decoration.set([currentLineDeco.range(line.from)]);
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

// --- breakpoint gutter (source pane) ------------------------------------

/** Replace the set of breakpoints shown in the gutter. */
export const setBreakpoints = StateEffect.define<Breakpoint[]>();

/** The current breakpoints, updated by `setBreakpoints`. */
const breakpointState = StateField.define<Breakpoint[]>({
  create() {
    return [];
  },
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setBreakpoints)) return e.value;
    return value;
  },
});

/** A breakpoint carrying a condition, ignore-count, or temporary flag — shown
 *  distinctly from a plain one (a diamond + tooltip, not a bare dot). */
function isConditional(bp: Breakpoint): boolean {
  return Boolean(bp.cond) || Boolean(bp.temporary) || (bp.ignore ?? 0) > 0;
}

/** Human-readable summary of a breakpoint's options, for the marker tooltip. */
function breakpointTitle(bp: Breakpoint): string {
  const parts: string[] = [];
  if (bp.cond) parts.push(`if ${bp.cond}`);
  if (bp.temporary) parts.push("temporary");
  if (bp.ignore) parts.push(`ignore ${bp.ignore}`);
  return parts.length ? `Breakpoint (${parts.join(", ")})` : "Breakpoint";
}

function markerFor(bp: Breakpoint | undefined): GutterMarker {
  return new (class extends GutterMarker {
    toDOM() {
      const span = document.createElement("span");
      if (!bp) {
        // A transparent slot on every non-breakpoint line so the whole gutter
        // column is clickable (and hints on hover — see the theme).
        span.className = "cm-breakpoint-slot";
        span.textContent = "●";
      } else if (isConditional(bp)) {
        span.className = "cm-breakpoint cm-breakpoint-cond";
        span.textContent = "◆";
        span.title = breakpointTitle(bp);
      } else {
        span.className = "cm-breakpoint";
        span.textContent = "●";
        span.title = breakpointTitle(bp);
      }
      return span;
    }
  })();
}

const breakpointSlot = markerFor(undefined);

/**
 * Clickable breakpoint gutter.
 *
 * A left click toggles the line via `onToggle`. Right-clicking a line to edit
 * its condition is handled by the source pane (a `contextmenu` listener that can
 * both open the editor and cancel the native menu — see SourcePane), so the
 * gutter itself only needs the plain toggle.
 */
function breakpointGutter(onToggle: (line: number) => void) {
  return [
    breakpointState,
    gutter({
      class: "cm-breakpoint-gutter",
      // A marker on *every* line (breakpoint or a transparent slot) keeps each
      // gutter cell wide enough to click — empty cells collapse to zero width.
      lineMarker(view, block) {
        const n = view.state.doc.lineAt(block.from).number;
        return markerFor(view.state.field(breakpointState).find((b) => b.line === n));
      },
      lineMarkerChange: (update) =>
        update.transactions.some((tr) =>
          tr.effects.some((e) => e.is(setBreakpoints)),
        ),
      initialSpacer: () => breakpointSlot,
      domEventHandlers: {
        mousedown(view, block, event) {
          // Left click only — a right click is the condition editor (handled in
          // the source pane so it can also cancel the native context menu).
          if ((event as MouseEvent).button !== 0) return false;
          onToggle(view.state.doc.lineAt(block.from).number);
          return true;
        },
      },
    }),
  ];
}

/**
 * Read-only source view: line numbers, Python highlight, current-line field.
 *
 * Pass `onToggleBreakpoint` to add a clickable breakpoint gutter (a left click
 * toggles a plain breakpoint); feed its dots with `setBreakpoints` effects. The
 * condition editor (right click) is wired up by the source pane, not here.
 */
export function sourceExtensions(onToggleBreakpoint?: (line: number) => void) {
  return [
    ...(onToggleBreakpoint ? breakpointGutter(onToggleBreakpoint) : []),
    lineNumbers(),
    python(),
    syntaxHighlighting(judbHighlight),
    currentLineField,
    judbTheme,
    EditorView.editable.of(false),
    EditorState.readOnly.of(true),
    EditorView.lineWrapping,
  ];
}

/**
 * Editable console-cell view: Python highlight, no line-number gutter.
 *
 * Pass a `completionSource` (backed by the backend `complete` round-trip) to get
 * Tab / as-you-type completion against the paused frame's namespace.
 */
export function cellExtensions(completionSource?: CompletionSource) {
  const base = [
    python(),
    syntaxHighlighting(judbHighlight),
    judbTheme,
    EditorView.lineWrapping,
  ];
  if (!completionSource) return base;
  return [
    ...base,
    autocompletion({ override: [completionSource], icons: false }),
    // Tab asks for completions (Ctrl-Space also works via the default keymap).
    keymap.of([{ key: "Tab", run: startCompletion }]),
  ];
}
