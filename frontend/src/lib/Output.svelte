<script lang="ts">
  import Anser from "anser";
  import { marked } from "marked";
  import type { Output } from "../protocol";
  import { theme } from "./theme.svelte";
  import { htmlDoc, vizDoc, vizMime } from "./richOutput";

  let { output }: { output: Output } = $props();

  // ANSI -> HTML with inline styles; anser escapes HTML entities, so {@html} is
  // safe here (IPython streams + tracebacks carry ANSI colour codes).
  const ansi = (s: string) => Anser.ansiToHtml(s, { use_classes: false });

  // The "registry": ordered richest-first list of standard mimes we render inline.
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

  // Interactive viz specs (Vega / Vega-Lite / Plotly JSON) are a *fallback*: we
  // reach for them only when there's no self-contained image or text/html to
  // prefer (altair/plotly usually also emit text/html, which is proven and
  // offline-capable — see richOutput.ts). Guarding on that also stops the
  // text/plain repr those objects always ship from winning.
  const preferStandard = $derived(
    richMime === "image/png" ||
      richMime === "image/jpeg" ||
      richMime === "image/svg+xml" ||
      richMime === "text/html",
  );
  const viz = $derived(preferStandard ? undefined : vizMime(Object.keys(d)));

  // What (if anything) renders in the sandboxed iframe: a viz spec (CDN-loaded),
  // raw text/html, or Markdown parsed to HTML (marked — synchronous by default).
  // The iframe isolates it all (raw HTML / scripts can't touch this page — no
  // separate sanitizer needed) and carries Jupyter output CSS + a resize script.
  const iframeSrcdoc = $derived.by(() => {
    if (viz) return vizDoc(viz, d[viz], theme.resolved);
    if (richMime === "text/html") return htmlDoc(String(d["text/html"]), theme.resolved);
    if (richMime === "text/markdown")
      return htmlDoc(marked.parse(String(d["text/markdown"])) as string, theme.resolved);
    return "";
  });
  const useIframe = $derived(iframeSrcdoc !== "");

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
{:else if useIframe}
  <!-- Sandboxed: script-bearing HTML (plotly/bokeh), Markdown-derived HTML, and
       CDN-loaded viz specs (Vega/Plotly) can't touch this page. srcdoc carries
       Jupyter output CSS + a resize script (see richOutput.ts). -->
  <iframe
    bind:this={frameEl}
    class="out"
    title="output"
    sandbox="allow-scripts"
    srcdoc={iframeSrcdoc}
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
