<script lang="ts">
  import { conn } from "../lib/connection.svelte";
  import type { VarPath } from "../protocol";
  import VarNode from "./VarNode.svelte";

  // Top-level locals are just names; each becomes a `["name", n]` root path that
  // VarNode expands lazily (an `expand` round-trip) — we never serialize whole
  // objects eagerly (PHASE2_STACK.md §7).
  const rootPath = (name: string): VarPath => [["name", name]];
</script>

<div class="vars">
  {#if conn.locals.length === 0}
    <span class="empty">{conn.paused ? "(no locals)" : "—"}</span>
  {:else}
    {#each conn.locals as name (name)}
      <VarNode {name} path={rootPath(name)} />
    {/each}
  {/if}
</div>

<style>
  .vars {
    height: 100%;
    overflow: auto;
    padding: 0.35rem 0.6rem;
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .empty {
    color: var(--fg-faint);
  }
</style>
