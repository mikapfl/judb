<script lang="ts">
  import { conn } from "../lib/connection.svelte";
  // Phase 2 TODO (PHASE2_STACK.md §7): a `expand(path)` round-trip to fetch
  // lazy rich reprs. For now the backend sends local *names* only.
</script>

<div class="vars">
  {#if conn.locals.length === 0}
    <span class="empty">{conn.paused ? "(no locals)" : "—"}</span>
  {:else}
    <ul>
      {#each conn.locals as name (name)}
        <li>{name}</li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .vars {
    height: 100%;
    overflow: auto;
    padding: 0.35rem 0.6rem;
  }
  .empty {
    color: var(--fg-faint);
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    padding: 0.1rem 0;
    color: var(--fg);
  }
</style>
