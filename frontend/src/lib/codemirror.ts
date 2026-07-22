// Shared CodeMirror 6 building blocks for the source pane (read-only, current
// line) and the console cells (editable). Framework-agnostic: panes mount an
// EditorView into a node. See PHASE2_STACK.md §3.

import { EditorState, StateEffect, StateField } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  lineNumbers,
  type DecorationSet,
} from "@codemirror/view";
import { syntaxHighlighting, defaultHighlightStyle } from "@codemirror/language";
import { python } from "@codemirror/lang-python";

/** Dark theme roughly matching tokens.css so editors sit in the panes cleanly. */
export const judbTheme = EditorView.theme(
  {
    "&": { color: "var(--fg)", backgroundColor: "transparent", height: "100%" },
    ".cm-content": { fontFamily: "var(--font-mono)", caretColor: "var(--fg)" },
    ".cm-gutters": {
      backgroundColor: "transparent",
      color: "var(--fg-faint)",
      border: "none",
    },
    ".cm-activeLine": { backgroundColor: "transparent" },
    ".cm-current-line": { backgroundColor: "var(--accent-bg)" },
    ".cm-scroller": { fontFamily: "var(--font-mono)" },
    "&.cm-focused": { outline: "none" },
  },
  { dark: true },
);

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

/** Read-only source view: line numbers, Python highlight, current-line field. */
export function sourceExtensions() {
  return [
    lineNumbers(),
    python(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    currentLineField,
    judbTheme,
    EditorView.editable.of(false),
    EditorState.readOnly.of(true),
    EditorView.lineWrapping,
  ];
}

/** Editable console-cell view: Python highlight, no line-number gutter. */
export function cellExtensions() {
  return [
    python(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    judbTheme,
    EditorView.lineWrapping,
  ];
}
