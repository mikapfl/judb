import { render } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExceptionPane from "./ExceptionPane.svelte";
import { conn } from "../lib/connection.svelte";
import type { ExceptionInfo } from "../protocol";

const ZERO_DIV: ExceptionInfo = {
  type: "ZeroDivisionError",
  message: "division by zero",
  chain: [
    {
      type: "ZeroDivisionError",
      headline: ["ZeroDivisionError: division by zero"],
      frames: [
        {
          filename: "/tmp/demo/crash.py",
          lineno: 3,
          function: "divide",
          lines: ["def divide(a, b):", '    """doc"""', "    return a / b"],
          first_lineno: 1,
          col: 11,
          end_col: 16,
        },
      ],
    },
  ],
};

describe("ExceptionPane component", () => {
  beforeEach(() => {
    conn.status = "paused"; // frame headers only act while the debuggee is paused
    conn.stack = [];
    conn.selected = 0;
  });

  it("renders 'No exception.' when conn.exception is null", () => {
    conn.exception = null;
    const { container } = render(ExceptionPane);
    expect(container.textContent).toContain("No exception.");
    expect(container.querySelector(".exc-tb")).toBeFalsy();
  });

  it("renders the header, the frame location, and the numbered source window", () => {
    conn.exception = ZERO_DIV;
    const { container } = render(ExceptionPane);

    expect(container.querySelector(".exc-type")?.textContent).toBe("ZeroDivisionError");
    expect(container.querySelector(".exc-msg")?.textContent).toBe("division by zero");

    // Location: basename shown, full path in the title (for narrow panes).
    const loc = container.querySelector(".frame-loc");
    expect(loc?.textContent?.replace(/\s+/g, " ").trim()).toBe("crash.py:3 in divide");
    expect(loc?.getAttribute("title")).toBe("/tmp/demo/crash.py");

    const gutters = [...container.querySelectorAll(".row .gutter")].map(
      (n) => n.textContent,
    );
    expect(gutters).toContain("1");
    expect(gutters).toContain("3");
    expect(container.querySelector(".headline")?.textContent).toBe(
      "ZeroDivisionError: division by zero",
    );
  });

  it("highlights the source with the editors' own theme, not backend colours", () => {
    conn.exception = ZERO_DIV;
    const { container } = render(ExceptionPane);
    const src = container.querySelector(".row .src");
    // Tokens carry CodeMirror HighlightStyle classes (which resolve to the
    // --tok-* variables), so a theme change recolours the traceback.
    expect(src?.querySelector("span")).toBeTruthy();
    // ...and nothing came pre-coloured over the wire.
    expect(container.innerHTML).not.toContain("\x1b[");
    expect(container.innerHTML).not.toContain("ansi-");
  });

  it("marks the failing line and anchors the failing sub-expression", () => {
    conn.exception = ZERO_DIV;
    const { container } = render(ExceptionPane);

    const current = container.querySelector(".row.current");
    expect(current?.querySelector(".gutter")?.textContent).toBe("3");
    // col/end_col cover "a / b" in "    return a / b".
    expect(container.querySelector(".anchor .src")?.textContent).toBe(
      " ".repeat(11) + "^".repeat(5),
    );
  });

  it("labels a chained cause the way Python does", () => {
    conn.exception = {
      type: "ValueError",
      message: "wrapped",
      chain: [
        {
          type: "ZeroDivisionError",
          headline: ["ZeroDivisionError: division by zero"],
          frames: [],
        },
        {
          type: "ValueError",
          headline: ["ValueError: wrapped"],
          frames: [],
          relation: "cause",
        },
      ],
    };
    const { container } = render(ExceptionPane);
    const relations = [...container.querySelectorAll(".relation")].map(
      (n) => n.textContent,
    );
    expect(relations).toEqual([
      "The above exception was the direct cause of the following exception:",
    ]);
  });

  it("makes a frame that is still on the stack selectable", async () => {
    conn.exception = ZERO_DIV;
    conn.stack = [
      { filename: "/tmp/demo/crash.py", lineno: 9, function: "main" },
      { filename: "/tmp/demo/crash.py", lineno: 3, function: "divide" },
    ];
    conn.selected = 1;
    const selectFrame = vi.spyOn(conn, "selectFrame").mockImplementation(() => {});

    const { container } = render(ExceptionPane);
    const button = container.querySelector<HTMLButtonElement>("button.frame-loc");
    expect(button?.textContent).toContain("crash.py:3");
    // It is the selected frame, so it reads as such.
    expect(button?.classList.contains("selected")).toBe(true);

    button?.click();
    expect(selectFrame).toHaveBeenCalledWith(1);
    selectFrame.mockRestore();
  });

  it("leaves a frame that has already unwound inert", () => {
    conn.exception = ZERO_DIV;
    // Nothing in the stack matches (a chained cause, or a stale traceback).
    conn.stack = [{ filename: "/tmp/demo/other.py", lineno: 3, function: "divide" }];
    const { container } = render(ExceptionPane);
    expect(container.querySelector("button.frame-loc")).toBeNull();
    expect(container.querySelector("div.frame-loc")?.textContent).toContain(
      "crash.py:3",
    );
  });
});
