<script lang="ts">
  import { conn } from "../lib/connection.svelte";

  const base = (path: string) => path.split("/").pop() ?? path;

  // Phase 2 TODO (§7): clicking a frame should send `select_frame(index)` so the
  // source/vars/console retarget. The innermost (paused) frame is the last one.
</script>

<div class="stack">
  {#if conn.stack.length === 0}
    <span class="empty">—</span>
  {:else}
    <ul>
      {#each conn.stack as frame, i (i)}
        <li class:current={i === conn.stack.length - 1}>
          <span class="fn">{frame.function}</span>
          <span class="loc">{base(frame.filename)}:{frame.lineno}</span>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .stack {
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
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.15rem 0.35rem;
    border-radius: 3px;
  }
  li.current {
    background: var(--accent-bg);
  }
  li.current .fn {
    color: var(--accent);
  }
  .loc {
    color: var(--fg-dim);
    white-space: nowrap;
  }
</style>
