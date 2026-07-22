import { render } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import Output from "./Output.svelte";
import type { Output as OutputData } from "../protocol";

const out = (o: OutputData) => render(Output, { output: o });

describe("Output renderer registry", () => {
  it("renders image/png as a base64 data URI", () => {
    const { container } = out({ kind: "display_data", data: { "image/png": "AAAA" } });
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "data:image/png;base64,AAAA",
    );
  });

  it("renders text/html in a script-sandboxed iframe", () => {
    const { container } = out({ kind: "display_data", data: { "text/html": "<b>hi</b>" } });
    const iframe = container.querySelector("iframe");
    expect(iframe?.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe?.getAttribute("srcdoc")).toContain("<b>hi</b>");
  });

  it("prefers a richer mime (png) over text/plain", () => {
    const { container } = out({
      kind: "execute_result",
      data: { "image/png": "AAAA", "text/plain": "<repr>" },
    });
    expect(container.querySelector("img")).toBeTruthy();
    expect(container.querySelector("pre")).toBeFalsy();
  });

  it("renders an error from ename/evalue when no traceback", () => {
    const { container } = out({
      kind: "error",
      data: { ename: "ValueError", evalue: "boom", traceback: [] },
    });
    const pre = container.querySelector("pre.error");
    expect(pre?.textContent).toContain("ValueError: boom");
  });

  it("marks stderr streams distinctly", () => {
    const { container } = out({ kind: "stream", data: { name: "stderr", text: "oops" } });
    expect(container.querySelector("pre.stderr")?.textContent).toBe("oops");
  });

  it("renders ANSI in text/plain as coloured HTML (e.g. `obj?` introspection)", () => {
    // "\x1b[31mSignature\x1b[39m" — red then reset, the shape pinfo emits.
    const { container } = out({
      kind: "display_data",
      data: { "text/plain": "[31mSignature[39m: greet(name)" },
    });
    const pre = container.querySelector("pre.out");
    expect(pre?.textContent).toContain("Signature: greet(name)");
    // anser turned the escape into a styled span rather than leaving raw codes.
    expect(pre?.querySelector("span")).toBeTruthy();
    expect(pre?.innerHTML).not.toContain("[");
  });
});
