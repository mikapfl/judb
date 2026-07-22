<script lang="ts">
  import type { Snippet } from "svelte";
  let { title, children }: { title: string; children: Snippet } = $props();
</script>

<div class="panebox">
  <h2>{title}</h2>
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
  .body {
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
</style>
