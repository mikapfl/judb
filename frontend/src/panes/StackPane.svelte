<script lang="ts">
  import { conn } from "../lib/connection.svelte";

  const base = (path: string) => path.split("/").pop() ?? path;

  // Clicking a frame retargets source / variables / console to it (§7).
  // The innermost (paused) frame is the last one.
</script>

<div class="stack">
  {#if conn.stack.length === 0}
    <span class="empty">—</span>
  {:else}
    <ul>
      {#each conn.stack as frame, i (i)}
        <li>
          <button
            class:selected={i === conn.selected}
            disabled={!conn.paused}
            onclick={() => conn.selectFrame(i)}
          >
            <span class="fn">{frame.function}</span>
            <span class="loc">{base(frame.filename)}:{frame.lineno}</span>
          </button>
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
    margin: 0;
  }
  /* Rows are buttons for keyboard/click a11y; strip the default button chrome. */
  button {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    width: 100%;
    padding: 0.15rem 0.35rem;
    border: none;
    border-radius: 3px;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  button:hover:not(:disabled) {
    background: var(--bg-raised);
  }
  button:disabled {
    opacity: 1;
    cursor: default;
  }
  button.selected {
    background: var(--accent-bg);
  }
  button.selected .fn {
    color: var(--accent);
  }
  .loc {
    color: var(--fg-dim);
    white-space: nowrap;
  }
</style>
