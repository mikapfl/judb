<script lang="ts">
  import { conn } from "../lib/connection.svelte";
  import type { BreakpointLocation } from "../protocol";

  // Basename, tolerant of both POSIX and Windows separators.
  const base = (path: string) => path.split(/[\\/]/).pop() ?? path;

  // Group breakpoints by file, keeping the backend's (filename, line) order.
  const groups = $derived.by(() => {
    const map = new Map<string, BreakpointLocation[]>();
    for (const bp of conn.allBreakpoints) {
      const list = map.get(bp.filename) ?? [];
      list.push(bp);
      map.set(bp.filename, list);
    }
    return [...map.entries()];
  });

  // The non-condition options (temporary / ignore) as a short label.
  function options(bp: BreakpointLocation): string {
    const parts: string[] = [];
    if (bp.temporary) parts.push("temporary");
    if (bp.ignore) parts.push(`ignore ${bp.ignore}`);
    return parts.join(" · ");
  }
</script>

<div class="breakpoints">
  {#if conn.allBreakpoints.length === 0}
    <span class="empty">No breakpoints</span>
  {:else}
    {#each groups as [filename, bps] (filename)}
      <div class="file" title={filename}>{base(filename)}</div>
      <ul>
        {#each bps as bp (bp.line)}
          <li class:conditional={bp.cond || bp.temporary || bp.ignore}>
            <span class="marker">{bp.cond || bp.temporary || bp.ignore ? "◆" : "●"}</span>
            <!-- Cross-file navigation: show the breakpoint's own file in the
                 Source pane, scrolled to it (its file may be one no frame is
                 in — that is exactly `open_file`'s job). -->
            <button
              class="line"
              title="Show this line in the source"
              onclick={() => conn.openFile(bp.filename, bp.line)}
            >
              line {bp.line}
            </button>
            {#if bp.cond}<code class="cond" title={bp.cond}>if {bp.cond}</code>{/if}
            {#if options(bp)}<span class="opts">{options(bp)}</span>{/if}
            <button
              class="remove"
              title="Remove breakpoint"
              aria-label="Remove breakpoint"
              disabled={!conn.paused}
              onclick={() => conn.clearBreak(bp.line, bp.filename)}
            >
              ×
            </button>
          </li>
        {/each}
      </ul>
    {/each}
  {/if}
</div>

<style>
  .breakpoints {
    height: 100%;
    overflow: auto;
    padding: 0.35rem 0.6rem;
  }
  .empty {
    color: var(--fg-faint);
  }
  .file {
    margin: 0.4rem 0 0.15rem;
    color: var(--fg-dim);
    font-size: 0.8rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .file:first-child {
    margin-top: 0;
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    padding: 0.1rem 0.2rem;
    border-radius: 3px;
  }
  li:hover {
    background: var(--bg-raised);
  }
  .marker {
    color: var(--err-fg);
    font-size: 0.7rem;
  }
  li.conditional .marker {
    color: var(--warn-fg);
  }
  .line {
    white-space: nowrap;
    padding: 0 0.15rem;
    border: none;
    border-radius: 3px;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .line:hover {
    background: var(--accent-bg);
    color: var(--fg);
  }
  .cond {
    color: var(--warn-fg);
    font-family: var(--font-mono);
    font-size: 0.8rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .opts {
    color: var(--fg-dim);
    font-size: 0.75rem;
    white-space: nowrap;
  }
  /* Push the remove button to the right edge of the row. */
  .remove {
    margin-left: auto;
    padding: 0 0.3rem;
    border: none;
    background: transparent;
    color: var(--fg-faint);
    font-size: 1rem;
    line-height: 1;
    cursor: pointer;
    border-radius: 3px;
  }
  .remove:hover:not(:disabled) {
    color: var(--err-fg);
    background: var(--bg);
  }
  .remove:disabled {
    opacity: 0.4;
    cursor: default;
  }
</style>
