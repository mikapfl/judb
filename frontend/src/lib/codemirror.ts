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
  // Clickable gutter: a red dot marks a set breakpoint; every other line has a
  // transparent slot that reveals a faint dot on hover, inviting a click.
  ".cm-breakpoint-gutter": { width: "1.1em", cursor: "pointer" },
  ".cm-breakpoint-gutter .cm-gutterElement": { paddingLeft: "0.15em" },
  ".cm-breakpoint": { color: "var(--err-fg, #e06c75)" },
  ".cm-breakpoint-slot": { color: "transparent" },
  ".cm-breakpoint-gutter .cm-gutterElement:hover .cm-breakpoint-slot": {
    color: "var(--err-fg, #e06c75)",
    opacity: "0.4",
  },
});

/** Python syntax palette, coloured via CSS variables (tokens.css) so it recolours
 *  on a theme switch. Replaces CodeMirror's fixed `defaultHighlightStyle`. */
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

/** Replace the set of breakpoint lines (1-based) shown in the gutter. */
export const setBreakpoints = StateEffect.define<number[]>();

/** The current breakpoint lines, updated by `setBreakpoints`. */
const breakpointLines = StateField.define<number[]>({
  create() {
    return [];
  },
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setBreakpoints)) return e.value;
    return value;
  },
});

function dotMarker(cls: string): GutterMarker {
  return new (class extends GutterMarker {
    toDOM() {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = "●";
      return span;
    }
  })();
}

// A red dot on a set breakpoint; a transparent slot on every other line so the
// whole gutter column is clickable (and hints on hover — see the theme).
const breakpointMarker = dotMarker("cm-breakpoint");
const breakpointSlot = dotMarker("cm-breakpoint-slot");

/** Clickable breakpoint gutter; a click toggles the line via `onToggle`. */
function breakpointGutter(onToggle: (line: number) => void) {
  return [
    breakpointLines,
    gutter({
      class: "cm-breakpoint-gutter",
      // A marker on *every* line (breakpoint or a transparent slot) keeps each
      // gutter cell wide enough to click — empty cells collapse to zero width.
      lineMarker(view, block) {
        const n = view.state.doc.lineAt(block.from).number;
        return view.state.field(breakpointLines).includes(n)
          ? breakpointMarker
          : breakpointSlot;
      },
      lineMarkerChange: (update) =>
        update.transactions.some((tr) =>
          tr.effects.some((e) => e.is(setBreakpoints)),
        ),
      initialSpacer: () => breakpointSlot,
      domEventHandlers: {
        mousedown(view, block) {
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
 * Pass `onToggleBreakpoint` to add a clickable breakpoint gutter; feed its
 * dots with `setBreakpoints` effects.
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
