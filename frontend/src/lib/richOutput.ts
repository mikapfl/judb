// Builders for the rich outputs that render inside the sandboxed <iframe> in
// Output.svelte. Two kinds:
//
//   * HTML / Markdown — wrapped with a compact analogue of Jupyter's output CSS
//     (`.dataframe` styling + generic rules) so any `_repr_html_` looks like a
//     notebook output. Derived from Project Jupyter's notebook/nbconvert
//     stylesheets (BSD-3-Clause; see NOTICE and licenses/jupyter-LICENSE.txt).
//
//   * Interactive viz specs — the Vega / Vega-Lite / Plotly JSON mime bundles,
//     rendered by loading the library from a CDN *inside* the sandboxed iframe.
//     Lazy (the ~1–4 MB library only loads when such an output actually appears,
//     so the single-file bundle stays small) and isolated (the iframe can't
//     touch this page). This mirrors how altair's `to_html()` / plotly's
//     `include_plotlyjs="cdn"` work. It is a *fallback*: altair and plotly
//     usually also emit a self-contained `text/html`, which Output.svelte
//     prefers (proven, offline-capable) over this path.
//
// Pure string builders, so they're unit-testable. `<\/script>` is written
// escaped throughout so the literal bytes never contain `</script>` — otherwise
// they'd close the inlined bundle's <script> when vite-plugin-singlefile inlines
// this module into index.html.

export type Resolved = "light" | "dark";

const PALETTE = {
  light: { bg: "#ffffff", fg: "#000000", rule: "#000000", link: "#0969da", hover: "rgba(66, 165, 245, 0.15)" },
  dark: { bg: "#1e1e22", fg: "#d6d6dc", rule: "#6a6a76", link: "#6cb6ff", hover: "rgba(154, 176, 255, 0.12)" },
} as const;

// Reports content height to the parent so the iframe fits its content (charts
// render asynchronously after the CDN scripts load, so the ResizeObserver — not
// just `load` — is what catches their final size).
const RESIZE_BODY = `
    function s(){ parent.postMessage({ judbHeight: Math.ceil(document.documentElement.scrollHeight) }, "*"); }
    addEventListener("load", s);
    if (window.ResizeObserver) new ResizeObserver(s).observe(document.body);
    else setTimeout(s, 50);`;
const RESIZE = `<script>${RESIZE_BODY}<\/script>`;

/** `<!doctype>` + Jupyter-derived output CSS, baked for the resolved theme (the
 *  sandboxed iframe can't read the app's CSS variables). */
export function outputHead(theme: Resolved): string {
  const c = PALETTE[theme];
  // `data-theme` on <html> lets library reprs that ship their own dark palette
  // (e.g. xarray keys off `html[data-theme="dark"]`) flip with us — without it
  // they render their light palette's dark text on our dark background.
  return `<!doctype html><html data-theme="${theme}"><meta charset="utf-8"><style>
    :root { color-scheme: ${theme}; }
    html, body { margin: 0; }
    body { padding: 4px 2px; background: ${c.bg}; color: ${c.fg};
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 13px; line-height: 1.4; }
    /* generic elements (low specificity — library reprs override freely) */
    h1, h2, h3, h4, h5, h6 { margin: 0.6em 0 0.3em; line-height: 1.2; font-weight: 600; }
    h1 { font-size: 1.6em; } h2 { font-size: 1.35em; }
    h3 { font-size: 1.15em; } h4 { font-size: 1.02em; }
    p { margin: 0.4em 0; }
    ul, ol { margin: 0.3em 0; padding-left: 1.6em; }
    li { margin: 0.1em 0; }
    a { color: ${c.link}; text-decoration: none; }
    a:hover { text-decoration: underline; }
    code, pre, kbd, samp { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.92em; }
    code { background: rgba(128, 128, 128, 0.15); padding: 0.1em 0.3em; border-radius: 3px; }
    pre { background: rgba(128, 128, 128, 0.12); padding: 0.6em 0.8em; border-radius: 4px; overflow: auto; }
    pre code { background: none; padding: 0; }
    blockquote { margin: 0.5em 0; padding: 0.1em 0.9em; border-left: 3px solid rgba(128, 128, 128, 0.4); }
    img { max-width: 100%; }
    hr { border: none; border-top: 1px solid rgba(128, 128, 128, 0.3); }
    table { border-collapse: collapse; border: none; }
    th, td { padding: 0.3em 0.6em; text-align: left; vertical-align: top; }
    thead th { border-bottom: 1px solid rgba(128, 128, 128, 0.5); font-weight: 600; }
    /* pandas DataFrames (higher specificity — right-aligned notebook look) */
    .dataframe, table.dataframe { border: none; margin: 0; }
    .dataframe th, .dataframe td { border: none; padding: 0.35em 0.6em;
      text-align: right; vertical-align: middle; white-space: nowrap; line-height: 1.3; }
    .dataframe thead th { border-bottom: 1px solid ${c.rule}; font-weight: 600; }
    .dataframe tbody th { font-weight: 600; text-align: right; }
    .dataframe tbody tr:hover { background: ${c.hover}; }
  </style>`;
}

/** Full srcdoc for an HTML body (raw `text/html` or Markdown-rendered HTML). */
export function htmlDoc(bodyHtml: string, theme: Resolved): string {
  return `${outputHead(theme)}<body data-theme="${theme}">${bodyHtml}${RESIZE}`;
}

// --- interactive viz specs (CDN-loaded inside the sandboxed iframe) ----------

// jsDelivr, major-pinned: only fetched when such an output appears.
const CDN = {
  vega: "https://cdn.jsdelivr.net/npm/vega@5",
  vegaLite: (major: number) => `https://cdn.jsdelivr.net/npm/vega-lite@${major}`,
  vegaEmbed: "https://cdn.jsdelivr.net/npm/vega-embed@6",
  plotly: "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2",
};

export const VEGALITE_RE = /^application\/vnd\.vegalite\.v(\d+)\+json$/;
export const VEGA_RE = /^application\/vnd\.vega\.v(\d+)\+json$/;
export const PLOTLY_MIME = "application/vnd.plotly.v1+json";

/** The interactive-viz mime in a bundle, if any (Vega-Lite / Vega / Plotly). */
export function vizMime(mimes: string[]): string | undefined {
  return mimes.find((m) => VEGALITE_RE.test(m) || VEGA_RE.test(m) || m === PLOTLY_MIME);
}

// Embed the spec as a JS literal; escape `<` so a value can't break out of the
// <script> (covers both `</script>` and `<!--`).
const jsonLiteral = (x: unknown) => JSON.stringify(x ?? null).replace(/</g, "\\u003c");
const scriptTag = (src: string) => `<script src="${src}"><\/script>`;
const vizHead = (theme: Resolved) =>
  `${outputHead(theme)}<style>#vis { width: 100%; } .viz-error { color: #c0392b; padding: 6px; }</style>`;

function vegaDoc(spec: unknown, vegaLiteMajor: number | null, theme: Resolved): string {
  const libs =
    scriptTag(CDN.vega) +
    (vegaLiteMajor ? scriptTag(CDN.vegaLite(vegaLiteMajor)) : "") +
    scriptTag(CDN.vegaEmbed);
  return `${vizHead(theme)}<body><div id="vis"></div>${libs}<script>
    vegaEmbed("#vis", ${jsonLiteral(spec)}, { actions: false${theme === "dark" ? ', theme: "dark"' : ""} })
      .catch(function (e) { document.body.innerHTML = '<div class="viz-error">Vega render failed: ' + e + '</div>'; });
  ${RESIZE_BODY}<\/script>`;
}

function plotlyDoc(fig: unknown, theme: Resolved): string {
  return `${vizHead(theme)}<body><div id="vis"></div>${scriptTag(CDN.plotly)}<script>
    try {
      var f = ${jsonLiteral(fig)};
      Plotly.newPlot("vis", f.data || [], f.layout || {}, Object.assign({ responsive: true, displaylogo: false }, f.config || {}));
    } catch (e) { document.body.innerHTML = '<div class="viz-error">Plotly render failed: ' + e + '</div>'; }
  ${RESIZE_BODY}<\/script>`;
}

/** srcdoc that renders a Vega/Vega-Lite/Plotly spec, given its mime + payload. */
export function vizDoc(mime: string, spec: unknown, theme: Resolved): string {
  const vl = VEGALITE_RE.exec(mime);
  if (vl) return vegaDoc(spec, Number(vl[1]), theme);
  if (VEGA_RE.test(mime)) return vegaDoc(spec, null, theme);
  return plotlyDoc(spec, theme); // PLOTLY_MIME
}
