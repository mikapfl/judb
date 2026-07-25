import { describe, expect, it } from "vitest";
import { ansiToHtml, mountAnsiPalette } from "./ansi";

describe("ANSI terminal output", () => {
  it("emits classes, not baked-in colours, so a theme switch repaints", () => {
    const html = ansiToHtml("\x1b[31mboom\x1b[39m");
    expect(html).toContain('class="ansi-red-fg"');
    expect(html).not.toContain("color:");
  });

  it("keeps bright colours distinct from their normal counterparts", () => {
    expect(ansiToHtml("\x1b[91mbright\x1b[39m")).toContain("ansi-bright-red-fg");
  });

  it("escapes HTML so the result is safe to {@html}", () => {
    expect(ansiToHtml("<script>")).toContain("&lt;script&gt;");
  });

  it("generates the fixed xterm-256 palette rules", () => {
    mountAnsiPalette();
    mountAnsiPalette();
    const styles = document.head.querySelectorAll('style[data-judb="ansi-palette"]');
    expect(styles).toHaveLength(1);
    const css = styles[0].textContent ?? "";
    // 16 is the first cube entry (pure black) and 231 the last (pure white);
    // 232-255 are the greyscale ramp starting at 8.
    expect(css).toContain(".ansi-palette-16-fg{color:rgb(0, 0, 0)}");
    expect(css).toContain(".ansi-palette-231-fg{color:rgb(255, 255, 255)}");
    expect(css).toContain(".ansi-palette-232-fg{color:rgb(8, 8, 8)}");
    // IPython's traceback formatter reaches for 28 (a green) — it must resolve.
    expect(css).toContain(".ansi-palette-28-fg{color:rgb(0, 135, 0)}");
    expect(css).not.toContain("--tok-"); // never syntax colours
  });
});
