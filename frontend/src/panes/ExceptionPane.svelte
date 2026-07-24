<script lang="ts">
  // The rightmost bottom pane: the exception behind a post-mortem pause (pytest
  // `--pdb`, or `-m judb` catching a crash). Empty when there is no exception —
  // an ordinary pause shows nothing here. Type + message sit at the top; the
  // full formatted traceback fills the rest as a scrollable block.
  import { conn } from "../lib/connection.svelte";
</script>

<div class="exception">
  {#if conn.exception}
    <div class="exc-head">
      <span class="exc-type">{conn.exception.type}</span>
      <span class="exc-msg">{conn.exception.message}</span>
    </div>
    {#if conn.exception.traceback?.length}
      <pre class="exc-tb">{conn.exception.traceback.join("")}</pre>
    {/if}
  {:else}
    <p class="empty">No exception.</p>
  {/if}
</div>

<style>
  .exception {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    color: var(--err-fg);
  }
  .exc-head {
    flex: none;
    padding: 0.4rem 0.5rem;
    border-bottom: 1px solid var(--err-fg);
    background: var(--err-bg);
  }
  .exc-type {
    font-weight: 700;
    font-family: var(--font-mono, monospace);
  }
  .exc-msg {
    margin-left: 0.4rem;
    opacity: 0.95;
    word-break: break-word;
  }
  .exc-tb {
    flex: 1;
    min-height: 0;
    margin: 0;
    overflow: auto;
    padding: 0.5rem;
    font-family: var(--font-mono, monospace);
    font-size: 0.8rem;
    line-height: 1.35;
    white-space: pre;
    color: var(--fg);
  }
  .empty {
    margin: 0;
    padding: 0.5rem;
    color: var(--fg-dim);
    font-style: italic;
  }
</style>
