<script lang="ts">
  import Anser from "anser";
  import { marked } from "marked";
  import type { Output } from "../protocol";
  import { theme } from "./theme.svelte";

  let { output }: { output: Output } = $props();

  // ANSI -> HTML with inline styles; anser escapes HTML entities, so {@html} is
  // safe here (IPython streams + tracebacks carry ANSI colour codes).
  const ansi = (s: string) => Anser.ansiToHtml(s, { use_classes: false });

  // The "registry": ordered richest-first list of mimes we render inline.
  // Adding @jupyterlab/rendermime later means extending this (PHASE2_STACK.md §4).
  const RICH_MIMES = [
    "image/png",
    "image/jpeg",
    "image/svg+xml",
    "text/html",
    "text/markdown",
    "application/json",
    "text/plain",
  ] as const;

  const d = $derived(output.data);
  const richMime = $derived(RICH_MIMES.find((m) => d[m] != null));

  const svgDataUri = (svg: string) =>
    `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;

  const errorText = $derived(
    (d.traceback && d.traceback.length
      ? d.traceback.join("\n")
      : `${d.ename ?? "Error"}: ${d.evalue ?? ""}`),
  );

  // The srcdoc iframe is script-safe (sandbox) but ships *no* stylesheet, so a
  // rich `_repr_html_` fell through to the browser's UA styling (pandas'
  // `<table border="1">` → 1990s beveled borders; bare links/code/headings). We
  // inject a compact analogue of Jupyter's output CSS: `.dataframe` styling plus
  // generic rules (tables, headings, lists, links, code/pre, blockquotes) so any
  // repr looks like a notebook output. This styling is derived from Project
  // Jupyter's notebook/nbconvert stylesheets (BSD-3-Clause; see NOTICE and
  // licenses/jupyter-LICENSE.txt). Heavy library reprs (Styler, xarray,
  // sklearn, plotly) ship their own scoped CSS and override these low-specificity
  // rules untouched. The iframe can't read the app's CSS variables, so colours
  // are baked in per resolved theme; neutral greys (rgba 128) work in both. A
  // tiny resize script reports content height back so the frame fits its content
  // instead of a fixed 24rem gap. `<\/script>` is escaped so it doesn't close
  // this component's own <script>.
  const PALETTE = {
    light: { bg: "#ffffff", fg: "#000000", rule: "#000000", link: "#0969da", hover: "rgba(66, 165, 245, 0.15)" },
    dark: { bg: "#1e1e22", fg: "#d6d6dc", rule: "#6a6a76", link: "#6cb6ff", hover: "rgba(154, 176, 255, 0.12)" },
  } as const;
  const htmlHead = (t: "light" | "dark") => {
    const c = PALETTE[t];
    return `<!doctype html><meta charset="utf-8"><style>
    :root { color-scheme: ${t}; }
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
  };
  const RESIZE_JS = `<script>
    function s(){ parent.postMessage({ judbHeight: Math.ceil(document.documentElement.scrollHeight) }, "*"); }
    addEventListener("load", s);
    if (window.ResizeObserver) new ResizeObserver(s).observe(document.body);
    else setTimeout(s, 50);
  <\/script>`;
  // Both text/html and text/markdown render in the sandboxed iframe: markdown is
  // parsed to HTML (marked), then it reuses the same isolation (so any raw HTML /
  // scripts a Markdown output carries can't touch this page — no separate
  // sanitizer needed) and the same Jupyter output CSS (headings, lists, code,
  // tables all styled). `marked.parse` is synchronous with the default options.
  const iframeMime = $derived(
    richMime === "text/html" || richMime === "text/markdown" ? richMime : undefined,
  );
  const iframeBody = $derived(
    richMime === "text/html"
      ? String(d["text/html"])
      : richMime === "text/markdown"
        ? (marked.parse(String(d["text/markdown"])) as string)
        : "",
  );
  const htmlDoc = $derived(
    iframeMime ? `${htmlHead(theme.resolved)}<body>${iframeBody}${RESIZE_JS}` : "",
  );

  // Grow the frame to its reported content height (capped; taller content scrolls
  // inside the frame). Only trust messages from *our* frame's window.
  const MAX_H = 480;
  let frameEl: HTMLIFrameElement | undefined = $state();
  let frameHeight = $state(0);
  $effect(() => {
    function onMsg(e: MessageEvent) {
      if (
        frameEl &&
        e.source === frameEl.contentWindow &&
        typeof (e.data as { judbHeight?: unknown })?.judbHeight === "number"
      ) {
        frameHeight = (e.data as { judbHeight: number }).judbHeight;
      }
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  });
</script>

{#if output.kind === "stream"}
  <pre class="out stream" class:stderr={d.name === "stderr"}>{@html ansi(d.text ?? "")}</pre>
{:else if output.kind === "error"}
  <pre class="out error">{@html ansi(errorText)}</pre>
{:else if richMime === "image/png" || richMime === "image/jpeg"}
  <img class="out" alt="output" src={`data:${richMime};base64,${d[richMime]}`} />
{:else if richMime === "image/svg+xml"}
  <img class="out" alt="output" src={svgDataUri(String(d["image/svg+xml"]))} />
{:else if iframeMime}
  <!-- Sandboxed: script-bearing HTML (plotly/bokeh) and Markdown-derived HTML
       can't touch this page. srcdoc carries Jupyter output CSS + a resize
       script (see htmlDoc). -->
  <iframe
    bind:this={frameEl}
    class="out"
    title="output"
    sandbox="allow-scripts"
    srcdoc={htmlDoc}
    style:height={`${Math.min(frameHeight || 40, MAX_H)}px`}
  ></iframe>
{:else if richMime === "application/json"}
  <pre class="out">{JSON.stringify(d["application/json"], null, 2)}</pre>
{:else if richMime === "text/plain"}
  <!-- text/plain often carries ANSI (e.g. `obj?` introspection); anser escapes
       HTML, so plain reprs render identically while coloured ones show right. -->
  <pre class="out">{@html ansi(String(d["text/plain"]))}</pre>
{:else if richMime}
  <pre class="out">{String(d[richMime])}</pre>
{/if}

<style>
  .out {
    margin: 0.4rem 0;
    max-width: 100%;
  }
  pre.out {
    margin: 0.4rem 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .stream.stderr,
  .error {
    color: var(--err-fg);
  }
  img.out {
    background: var(--img-bg);
    border-radius: 4px;
  }
  iframe.out {
    display: block;
    width: 100%;
    border: 0;
    background: var(--df-bg);
    border-radius: 4px;
  }
</style>
