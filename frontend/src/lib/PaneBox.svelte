<script lang="ts">
  import type { Snippet } from "svelte";
  // `onClose` is what makes a pane *secondary*: the Source and the console have
  // none, because an app with neither on screen isn't a debugger any more.
  let {
    title,
    children,
    onClose,
  }: { title: string; children: Snippet; onClose?: () => void } = $props();
</script>

<div class="panebox">
  <h2>
    <span class="title">{title}</span>
    {#if onClose}
      <button
        class="close"
        title="Hide this pane — re-open it from ▤ Panes in the toolbar"
        aria-label="Hide {title} pane"
        onclick={onClose}
      >
        ×
      </button>
    {/if}
  </h2>
  <div class="body">{@render children()}</div>
</div>

<style>
  .panebox {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0;
    padding: 0.4rem 0.75rem;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--fg-dim);
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
    height: var(--pane-header-h);
    flex: none;
  }
  .title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* Faint until the header is hovered: closing a pane is a deliberate act, not
     something to trip over while reaching for the splitter. */
  .close {
    flex: none;
    padding: 0 0.25rem;
    border: none;
    background: transparent;
    color: inherit;
    font-size: 1rem;
    line-height: 1;
    cursor: pointer;
    opacity: 0;
    border-radius: 3px;
  }
  h2:hover .close,
  .close:focus-visible {
    opacity: 0.7;
  }
  .close:hover {
    opacity: 1;
    color: var(--fg);
    background: var(--border);
  }
  .body {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
</style>
