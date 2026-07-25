// ANSI-coloured *terminal* output (stdout/stderr streams, `obj?` introspection,
// the console's own IPython traceback) rendered as themed HTML.
//
// The one rule here: ANSI is terminal colour, not syntax colour. The 16 named
// ANSI colours are a first-class part of a judb theme (`--ansi-*` in tokens.css,
// same as any terminal emulator's palette) and are deliberately *not* aliased to
// the `--tok-*` syntax palette — a debuggee that prints green must come out
// green, not "whatever colour numbers happen to be". Code we know is code goes
// through highlight.ts instead.
//
// Indices 16-255 are xterm's fixed colour cube and greyscale ramp. Those aren't
// a theme choice, they're a formula, so we generate their rules once at runtime
// rather than shipping 240 hand-written (or guessed) declarations.

import Anser from "anser";

/** xterm-256 channel levels for the 6x6x6 colour cube. */
const CUBE = [0, 95, 135, 175, 215, 255];

function paletteColor(n: number): string {
  if (n < 232) {
    const i = n - 16;
    const [r, g, b] = [Math.floor(i / 36) % 6, Math.floor(i / 6) % 6, i % 6];
    return `rgb(${CUBE[r]}, ${CUBE[g]}, ${CUBE[b]})`;
  }
  const level = 8 + 10 * (n - 232); // 24-step greyscale ramp
  return `rgb(${level}, ${level}, ${level})`;
}

function paletteRules(): string {
  const rules: string[] = [];
  for (let n = 16; n < 256; n++) {
    const color = paletteColor(n);
    rules.push(`.ansi-palette-${n}-fg{color:${color}}`);
    rules.push(`.ansi-palette-${n}-bg{background-color:${color}}`);
  }
  return rules.join("\n");
}

let styleEl: HTMLStyleElement | null = null;

/** Publish the fixed xterm-256 palette rules (idempotent). The 16 named colours
 *  live in tokens.css instead, because those *are* theme-owned. */
export function mountAnsiPalette(): void {
  if (styleEl?.isConnected) return;
  styleEl = document.createElement("style");
  styleEl.dataset.judb = "ansi-palette";
  styleEl.textContent = paletteRules();
  document.head.appendChild(styleEl);
}

/**
 * Convert ANSI-escaped text to HTML that follows the current theme.
 *
 * Uses anser's class mode (rather than baked-in inline styles) so the colours
 * resolve through CSS custom properties and a theme switch repaints them.
 *
 * `ansiToHtml` does *not* escape its input — it only recognises escape codes —
 * so we run `escapeForHtml` first (it leaves the ESC bytes alone). Skipping that
 * would let a debuggee inject markup into judb's own page just by printing it.
 * With it, the result is safe to `{@html}`.
 */
export function ansiToHtml(text: string): string {
  mountAnsiPalette();
  return Anser.ansiToHtml(Anser.escapeForHtml(text), { use_classes: true });
}
