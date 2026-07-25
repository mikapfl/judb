import { describe, expect, it } from "vitest";
import { highlightLines, mountHighlightStyles } from "./highlight";
import { judbHighlight } from "./codemirror";

describe("standalone Python highlighting", () => {
  it("splits at line breaks and tags tokens", () => {
    const lines = highlightLines("def f(x):\n    return x + 1");
    expect(lines).toHaveLength(2);
    // `def` is a keyword, so it must come out wrapped, not as bare text.
    expect(lines[0]).toMatch(/<span class="[^"]+">def<\/span>/);
    expect(lines[1]).toContain("    "); // indentation preserved verbatim
  });

  it("colours a keyword straight from --tok-keyword", () => {
    // The point of the whole design: follow the class a token actually got, and
    // it lands on the same CSS variable the source editor paints keywords with.
    const [line] = highlightLines("def f(): pass");
    const keywordClass = /<span class="([^"]+)">def<\/span>/.exec(line)?.[1];
    expect(keywordClass).toBeTruthy();

    const rules = judbHighlight.module?.getRules() ?? "";
    const rule = rules
      .split("}")
      .find((r) => r.includes(`.${keywordClass}`) || r.includes(`.${keywordClass},`));
    expect(rule).toContain("var(--tok-keyword)");
  });

  it("escapes HTML so the result is safe to {@html}", () => {
    const [line] = highlightLines('x = "<script>&"');
    expect(line).toContain("&lt;script&gt;&amp;");
    expect(line).not.toContain("<script>");
  });

  it("mounts the style rules once, resolving to --tok-* variables", () => {
    mountHighlightStyles();
    mountHighlightStyles();
    const styles = document.head.querySelectorAll('style[data-judb="highlight"]');
    expect(styles).toHaveLength(1);
    expect(styles[0].textContent).toContain("var(--tok-keyword)");
  });

  it("tolerates a fragment that is not a valid module on its own", () => {
    // Traceback windows are slices of a file — indented, often unbalanced.
    const lines = highlightLines("        return a / b\n    finally:");
    expect(lines).toHaveLength(2);
    expect(lines.join("")).toContain("return");
  });
});
