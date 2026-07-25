// Python syntax highlighting *outside* an EditorView.
//
// The Exception pane shows source lines that never live in an editor, but they
// must look exactly like the Source pane's — and keep looking like it when the
// theme changes. So we highlight them with the same `judbHighlight` object the
// editors use (a `HighlightStyle` is also a lezer `Highlighter`) instead of
// giving the traceback a palette of its own. Its generated CSS rules already
// resolve to the `--tok-*` custom properties from tokens.css, so a theme switch
// recolours tracebacks for free.
//
// This is why the backend ships traceback *structure* rather than ANSI-coloured
// text (judb/tracebacks.py): colour is decided here, once.

import { highlightCode } from "@lezer/highlight";
import { judbHighlight, pythonParser } from "./codemirror";

// `judbHighlight`'s classes are normally injected by the `syntaxHighlighting`
// extension when an editor mounts. We may render before (or without) any
// editor, so publish the same rules ourselves — once, idempotently.
let styleEl: HTMLStyleElement | null = null;

/** Ensure the highlight style's CSS rules are present in the document. */
export function mountHighlightStyles(): void {
  if (styleEl?.isConnected || !judbHighlight.module) return;
  styleEl = document.createElement("style");
  styleEl.dataset.judb = "highlight";
  styleEl.textContent = judbHighlight.module.getRules();
  document.head.appendChild(styleEl);
}

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
};

const escapeHtml = (s: string) => s.replace(/[&<>"]/g, (c) => ESCAPES[c]);

/**
 * Highlight a Python snippet, one HTML string per line.
 *
 * The whole snippet is parsed in one go (so a multi-line window highlights
 * coherently) and then split at the line breaks, which keeps every returned
 * string independently placeable — the Exception pane pairs each with its own
 * line number. The output is HTML-escaped, so callers can `{@html}` it.
 *
 * Note the snippet is usually a *fragment* of a file (an indented function
 * body, say). Lezer's parser is error-tolerant, so tokens still get their tags
 * even though the fragment isn't a valid module on its own.
 */
export function highlightLines(code: string): string[] {
  mountHighlightStyles();
  const lines: string[] = [];
  let current = "";
  highlightCode(
    code,
    pythonParser.parse(code),
    judbHighlight,
    (text, classes) => {
      const escaped = escapeHtml(text);
      current += classes ? `<span class="${classes}">${escaped}</span>` : escaped;
    },
    () => {
      lines.push(current);
      current = "";
    },
  );
  lines.push(current);
  return lines;
}
