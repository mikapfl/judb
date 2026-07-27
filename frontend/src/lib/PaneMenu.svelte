<script lang="ts">
  // The way back for a closed pane. A pane's × removes it from the layout
  // entirely, so without this menu there would be no affordance left to bring it
  // back — the one rule a hideable pane has to obey.
  import { layout, SECONDARY_PANES } from "./layout.svelte";

  let open = $state(false);

  const hiddenCount = $derived(layout.closed.length);
</script>

<div class="panemenu">
  <button
    class="trigger"
    class:has-hidden={hiddenCount > 0}
    aria-haspopup="true"
    aria-expanded={open}
    aria-label="Panes"
    title="Show or hide the lower panes"
    onclick={() => (open = !open)}
  >
    ▤<span class="label">Panes</span>{#if hiddenCount > 0}<span class="count">{hiddenCount} hidden</span>{/if}
  </button>

  {#if open}
    <!-- Click-away backdrop, as in the breakpoint editor popover. -->
    <div
      class="backdrop"
      role="presentation"
      onmousedown={() => (open = false)}
      oncontextmenu={(e) => e.preventDefault()}
    ></div>
    <div
      class="menu"
      role="menu"
      tabindex="-1"
      onkeydown={(e) => {
        if (e.key === "Escape") open = false;
      }}
    >
      {#each SECONDARY_PANES as pane (pane.id)}
        <label class="item">
          <input
            type="checkbox"
            checked={layout.isOpen(pane.id)}
            onchange={() => layout.toggle(pane.id)}
          />
          {pane.title}
        </label>
      {/each}
      <button class="all" disabled={hiddenCount === 0} onclick={() => layout.showAll()}>
        Show all
      </button>
    </div>
  {/if}
</div>

<style>
  .panemenu {
    position: relative;
    display: flex;
  }
  .trigger {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.25rem 0.6rem;
    line-height: 1;
  }
  .trigger.has-hidden {
    border-color: var(--accent);
  }
  .count {
    font-size: 10px;
    color: var(--accent);
  }
  /* Matches the toolbar's own narrow-window rule: glyph only, but the hidden
     count stays — it is the only hint that a pane is missing on purpose. */
  @media (max-width: 860px) {
    .label {
      display: none;
    }
  }
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 10;
  }
  .menu {
    position: absolute;
    z-index: 11;
    top: calc(100% + 4px);
    right: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    min-width: 12rem;
    padding: 0.4rem;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
    font-size: 0.85rem;
    text-transform: none;
  }
  .menu:focus {
    outline: none;
  }
  .item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.2rem 0.3rem;
    border-radius: 4px;
    cursor: pointer;
  }
  .item:hover {
    background: var(--accent-bg);
  }
  .all {
    margin-top: 0.25rem;
    padding: 0.2rem 0.4rem;
    font-size: 0.8rem;
  }
</style>
