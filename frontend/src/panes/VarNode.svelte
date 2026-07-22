<script lang="ts">
  import { conn } from "../lib/connection.svelte";
  import type { VarPath } from "../protocol";
  import Output from "../lib/Output.svelte";
  import Self from "./VarNode.svelte";

  // `summary` is absent for top-level locals (the backend sends names only); it
  // shows for children, whose one-line repr the parent's expand already fetched.
  let {
    name,
    path,
    summary = "",
  }: {
    name: string;
    path: VarPath;
    summary?: string;
  } = $props();

  let open = $state(false);
  // The fetched subtree for this node (repr + children), once expanded.
  const sub = $derived(conn.expansionOf(path));

  function toggle() {
    open = !open;
    if (open) conn.expand(path);
  }

  // Show the value's own rich repr (e.g. a DataFrame's HTML table) when it adds
  // something the child list doesn't: any non-text mime, or a plain leaf with no
  // children (so expanding a long string / scalar shows its full value).
  const showRepr = $derived.by(() => {
    const repr = sub?.repr;
    if (!repr) return false;
    const rich = Object.keys(repr).some((m) => m !== "text/plain");
    return rich || !(sub?.children?.length ?? 0);
  });
</script>

<div class="node">
  <button class="row" onclick={toggle} title={summary}>
    <span class="twist">{open ? "▾" : "▸"}</span>
    <span class="key">{name}</span>
    {#if summary}<span class="summary">{summary}</span>{/if}
  </button>

  {#if open}
    <div class="children">
      {#if !sub || sub.loading}
        <div class="hint">…</div>
      {:else if sub.error}
        <div class="err">{sub.error}</div>
      {:else}
        {#if showRepr && sub.repr}
          <div class="repr"><Output output={{ kind: "display_data", data: sub.repr }} /></div>
        {/if}
        {#each sub.children ?? [] as child (JSON.stringify(child.path))}
          <Self name={child.key} path={child.path} summary={child.summary} />
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .row {
    display: flex;
    align-items: baseline;
    gap: 0.35rem;
    width: 100%;
    padding: 0.1rem 0;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .row:hover {
    background: var(--accent-bg);
  }
  .twist {
    flex: 0 0 auto;
    color: var(--fg-faint);
    width: 1ch;
  }
  .key {
    color: var(--fg);
  }
  .summary {
    color: var(--fg-faint);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .children {
    margin-left: 1ch;
    border-left: 1px solid var(--border);
    padding-left: 0.4rem;
  }
  .hint {
    color: var(--fg-faint);
  }
  .err {
    color: var(--err-fg);
    white-space: pre-wrap;
  }
  .repr {
    margin: 0.1rem 0 0.3rem;
  }
</style>
