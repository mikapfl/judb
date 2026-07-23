import { describe, expect, it } from "vitest";
import { htmlDoc, vizDoc, vizMime } from "./richOutput";

describe("vizMime detection", () => {
  it("recognises Vega-Lite, Vega and Plotly mimes", () => {
    expect(vizMime(["text/plain", "application/vnd.vegalite.v5+json"])).toBe(
      "application/vnd.vegalite.v5+json",
    );
    expect(vizMime(["application/vnd.vega.v5+json"])).toBe("application/vnd.vega.v5+json");
    expect(vizMime(["application/vnd.plotly.v1+json"])).toBe("application/vnd.plotly.v1+json");
  });

  it("ignores non-viz mimes", () => {
    expect(vizMime(["text/html", "image/png", "application/json"])).toBeUndefined();
  });
});

describe("vizDoc", () => {
  it("loads vega + vega-lite (matching major) + vega-embed and embeds the spec", () => {
    const doc = vizDoc("application/vnd.vegalite.v4+json", { mark: "bar" }, "light");
    expect(doc).toContain("cdn.jsdelivr.net/npm/vega@5");
    expect(doc).toContain("cdn.jsdelivr.net/npm/vega-lite@4"); // major from the mime
    expect(doc).toContain("cdn.jsdelivr.net/npm/vega-embed@6");
    expect(doc).toContain('vegaEmbed("#vis", {"mark":"bar"}');
  });

  it("skips vega-lite for a plain Vega spec and passes the dark theme", () => {
    const doc = vizDoc("application/vnd.vega.v5+json", { $schema: "x" }, "dark");
    expect(doc).toContain("cdn.jsdelivr.net/npm/vega@5");
    expect(doc).not.toContain("vega-lite@");
    expect(doc).toContain('theme: "dark"');
  });

  it("loads plotly and calls newPlot with data/layout", () => {
    const doc = vizDoc(
      "application/vnd.plotly.v1+json",
      { data: [{ y: [1, 2] }], layout: { title: "t" } },
      "light",
    );
    expect(doc).toContain("cdn.jsdelivr.net/npm/plotly.js-dist-min@2");
    expect(doc).toContain("Plotly.newPlot");
    expect(doc).toContain('"data":[{"y":[1,2]}]');
  });

  it("escapes < so a spec value cannot break out of the <script>", () => {
    const doc = vizDoc("application/vnd.plotly.v1+json", { layout: { title: "</script><x>" } }, "light");
    expect(doc).not.toContain("</script><x>");
    expect(doc).toContain("\\u003c/script>\\u003cx>");
  });
});

describe("htmlDoc", () => {
  it("wraps a body with the Jupyter output CSS + resize script", () => {
    const doc = htmlDoc("<p>hi</p>", "light");
    expect(doc).toContain(".dataframe thead th { border-bottom");
    expect(doc).toContain("<p>hi</p>");
    expect(doc).toContain("judbHeight");
  });
});
