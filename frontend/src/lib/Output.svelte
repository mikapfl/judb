<script lang="ts">
  import Anser from "anser";
  import type { Output } from "../protocol";

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
</script>

{#if output.kind === "stream"}
  <pre class="out stream" class:stderr={d.name === "stderr"}>{@html ansi(d.text ?? "")}</pre>
{:else if output.kind === "error"}
  <pre class="out error">{@html ansi(errorText)}</pre>
{:else if richMime === "image/png" || richMime === "image/jpeg"}
  <img class="out" alt="output" src={`data:${richMime};base64,${d[richMime]}`} />
{:else if richMime === "image/svg+xml"}
  <img class="out" alt="output" src={svgDataUri(String(d["image/svg+xml"]))} />
{:else if richMime === "text/html"}
  <!-- Sandboxed: script-bearing HTML (plotly/bokeh) can't touch this page. -->
  <iframe class="out" title="output" sandbox="allow-scripts" srcdoc={String(d["text/html"])}></iframe>
{:else if richMime === "application/json"}
  <pre class="out">{JSON.stringify(d["application/json"], null, 2)}</pre>
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
    background: #fff;
    border-radius: 4px;
  }
  iframe.out {
    width: 100%;
    min-height: 24rem;
    border: 0;
    background: #fff;
    border-radius: 4px;
  }
</style>
