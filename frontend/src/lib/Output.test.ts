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

  it("renders text/markdown as HTML in the sandboxed iframe", () => {
    const { container } = out({
      kind: "display_data",
      data: { "text/markdown": "# Title\n\n- one\n- two" },
    });
    const srcdoc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    // marked turned the Markdown into HTML (heading + list), not raw source.
    expect(srcdoc).toContain("<h1>Title</h1>");
    expect(srcdoc).toContain("<li>one</li>");
    expect(srcdoc).not.toContain("# Title");
  });

  it("prefers text/html over text/markdown when both are present", () => {
    const { container } = out({
      kind: "execute_result",
      data: { "text/html": "<b>rich</b>", "text/markdown": "# md" },
    });
    const srcdoc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain("<b>rich</b>");
    expect(srcdoc).not.toContain("<h1>md</h1>");
  });

  it("renders a Vega-Lite spec via the CDN viz iframe", () => {
    const { container } = out({
      kind: "execute_result",
      data: {
        "application/vnd.vegalite.v5+json": { mark: "point" },
        "text/plain": "alt.Chart(...)",
      },
    });
    const srcdoc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain("vega-embed@6");
    expect(srcdoc).toContain('vegaEmbed("#vis", {"mark":"point"}');
    // The text/plain repr those objects always ship must not win.
    expect(container.querySelector("pre")).toBeFalsy();
  });

  it("renders a Plotly figure via the CDN viz iframe", () => {
    const { container } = out({
      kind: "execute_result",
      data: { "application/vnd.plotly.v1+json": { data: [{ y: [1] }], layout: {} } },
    });
    const srcdoc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain("plotly.js-dist-min@2");
    expect(srcdoc).toContain("Plotly.newPlot");
  });

  it("prefers self-contained text/html over the Plotly JSON mime", () => {
    const { container } = out({
      kind: "execute_result",
      data: {
        "text/html": "<div>self-contained plotly</div>",
        "application/vnd.plotly.v1+json": { data: [], layout: {} },
      },
    });
    const srcdoc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain("self-contained plotly");
    expect(srcdoc).not.toContain("Plotly.newPlot");
  });

  it("prefers a static image over the Plotly JSON mime", () => {
    const { container } = out({
      kind: "execute_result",
      data: { "image/png": "AAAA", "application/vnd.plotly.v1+json": { data: [], layout: {} } },
    });
    expect(container.querySelector("img")).toBeTruthy();
    expect(container.querySelector("iframe")).toBeFalsy();
  });

  it("injects Jupyter .dataframe CSS so pandas tables lose the UA borders", () => {
    // pandas emits <table border="1" class="dataframe">; without our CSS the
    // browser draws 1990s beveled cell borders. The srcdoc must ship the reset.
    const { container } = out({
      kind: "execute_result",
      data: { "text/html": '<table border="1" class="dataframe"><tr><td>1</td></tr></table>' },
    });
    const srcdoc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain(".dataframe th, .dataframe td { border: none");
    expect(srcdoc).toContain(".dataframe thead th { border-bottom");
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
