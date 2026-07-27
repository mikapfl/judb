<script lang="ts">
  import { untrack } from "svelte";
  import { Splitpanes, Pane } from "svelte-splitpanes";
  import Toolbar from "./lib/Toolbar.svelte";
  import PaneBox from "./lib/PaneBox.svelte";
  import SourcePane from "./panes/SourcePane.svelte";
  import ConsolePane from "./panes/ConsolePane.svelte";
  import VariablesPane from "./panes/VariablesPane.svelte";
  import WatchPane from "./panes/WatchPane.svelte";
  import StackPane from "./panes/StackPane.svelte";
  import BreakpointsPane from "./panes/BreakpointsPane.svelte";
  import ExceptionPane from "./panes/ExceptionPane.svelte";
  import { conn } from "./lib/connection.svelte";
  import { layout, SECONDARY_PANES, type PaneId } from "./lib/layout.svelte";

  $effect(() => {
    conn.connect();
  });

  const title = (id: PaneId) => SECONDARY_PANES.find((p) => p.id === id)!.title;

  // Share of the working area the source gets. It means width while the console
  // is beside it and height once the console has folded under — the same number
  // either way, so a fold doesn't throw away the split the user chose.
  let sourceSize = $state(50);

  // Enforce the 80-column floor on *window* resize. Dragging is already bounded
  // by the pane's `minSize`, but a shrinking window leaves percentages untouched
  // and would quietly squeeze the source below the floor.
  $effect(() => {
    const min = layout.sourceMinPct;
    if (layout.stackConsole) return;
    if (untrack(() => sourceSize) < min) sourceSize = Math.min(min, 85);
  });

  // How much height the secondary area gets — more of it once it has reflowed
  // into two or three rows, or none at all when every pane in it is closed.
  // Only reassigned when the row count actually changes, so a user's drag
  // survives everything else.
  let secondarySize = $state(20);
  let rowCount = layout.rows.length;
  $effect(() => {
    const rows = layout.rows.length;
    if (rows === rowCount) return;
    rowCount = rows;
    secondarySize = rows >= 3 ? 46 : rows === 2 ? 34 : 20;
  });

  // The finished overlay is dismissable so the user can still inspect whatever
  // was last on screen. Reset the dismissal if a fresh session ever reconnects.
  let finishedDismissed = $state(false);
  $effect(() => {
    if (conn.status !== "finished") finishedDismissed = false;
  });
</script>

<Toolbar />

<!-- A transient, dismissable notice (e.g. a breakpoint that could not be set). -->
{#if conn.notice}
  <div class="notice" role="alert">
    <span class="notice-text">{conn.notice}</span>
    <button
      class="notice-dismiss"
      aria-label="Dismiss notice"
      onclick={() => (conn.notice = null)}
    >
      ×
    </button>
  </div>
{/if}

<!-- The content of one secondary pane. They are laid out by id (see
     layout.svelte.ts), so the grid can reflow without knowing what's inside. -->
{#snippet secondaryPane(id: PaneId)}
  {#if id === "breakpoints"}
    <BreakpointsPane />
  {:else if id === "stack"}
    <StackPane />
  {:else if id === "variables"}
    <VariablesPane />
  {:else if id === "watch"}
    <WatchPane />
  {:else if id === "exception"}
    <ExceptionPane />
  {/if}
{/snippet}

<main {@attach layout.observe}>
  <!-- Primary divider is top/bottom: the working area (Source + Console) gets
       most of the height; the secondary panes reflow underneath. -->
  <Splitpanes horizontal theme="" class="judb-split">
    <Pane size={layout.rows.length ? 100 - secondarySize : 100} minSize={30}>
      <!-- Source + console: side by side while both fit, stacked (console
           under) once 80 columns of source and a usable console no longer do.
           One Splitpanes with a reactive `horizontal` rather than two behind an
           `{#if}`, so folding never remounts the console and loses cell text. -->
      <Splitpanes horizontal={layout.stackConsole} theme="" class="judb-split">
        <Pane
          bind:size={sourceSize}
          minSize={layout.stackConsole ? 20 : layout.sourceMinPct}
        >
          <PaneBox title="Source">
            <SourcePane />
          </PaneBox>
        </Pane>
        <Pane size={100 - sourceSize} minSize={15}>
          <PaneBox title="Notebook console — runs in the paused frame">
            <ConsolePane />
          </PaneBox>
        </Pane>
      </Splitpanes>
    </Pane>
    <!-- Nothing left open down here is a legitimate state: the whole row goes,
         and ▤ Panes in the toolbar is how it comes back. -->
    {#if layout.rows.length}
      <Pane bind:size={secondarySize} minSize={10}>
        <Splitpanes horizontal theme="" class="judb-split">
          {#each layout.rows as row, i (i)}
            <Pane size={100 / layout.rows.length} minSize={10}>
              <Splitpanes theme="" class="judb-split">
                {#each row as id (id)}
                  <Pane size={100 / row.length} minSize={10}>
                    <PaneBox
                      title={title(id)}
                      onClose={() => layout.setOpen(id, false)}
                    >
                      {@render secondaryPane(id)}
                    </PaneBox>
                  </Pane>
                {/each}
              </Splitpanes>
            </Pane>
          {/each}
        </Splitpanes>
      </Pane>
    {/if}
  </Splitpanes>
</main>

<!-- The debuggee has exited; nothing here can restart it. Grey the whole app
     out and float a prominent badge so it's obvious the session is over.
     Dismissable — click the scrim (or Esc) to inspect what's still on screen. -->
{#if conn.status === "finished" && !finishedDismissed}
  <div
    class="finished-overlay"
    role="button"
    tabindex="0"
    aria-label="Dismiss finished notice"
    {@attach (el) => el.focus()}
    onclick={() => (finishedDismissed = true)}
    onkeydown={(e) => {
      if (e.key === "Escape" || e.key === "Enter" || e.key === " ") finishedDismissed = true;
    }}
  >
    <div class="finished-badge">
      <span class="finished-title">Debuggee finished</span>
      <span class="finished-sub">The program has exited — nothing left to debug.</span>
      <span class="finished-hint">Click anywhere to dismiss and inspect the last state.</span>
    </div>
  </div>
{/if}

<style>
  main {
    flex: 1;
    min-height: 0;
  }
  /* Dismissable notice bar under the toolbar (breakpoint rejections, etc.). */
  .notice {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.4rem 0.75rem;
    background: var(--warn-bg);
    color: var(--warn-fg);
    border-bottom: 1px solid var(--warn-fg);
    font-size: 0.85rem;
  }
  .notice-text {
    flex: 1;
    min-width: 0;
  }
  .notice-dismiss {
    flex: none;
    padding: 0 0.4rem;
    border: none;
    background: transparent;
    color: inherit;
    font-size: 1.1rem;
    line-height: 1;
    cursor: pointer;
    border-radius: 3px;
  }
  .notice-dismiss:hover {
    background: rgba(0, 0, 0, 0.15);
  }
  /* Dark splitter styling (we opted out of the default light theme). */
  main :global(.splitpanes.judb-split) {
    background: var(--bg);
  }
  main :global(.judb-split > .splitpanes__splitter) {
    background: var(--border);
    position: relative;
  }
  main :global(.judb-split.splitpanes--vertical > .splitpanes__splitter) {
    width: 5px;
  }
  main :global(.judb-split.splitpanes--horizontal > .splitpanes__splitter) {
    height: 5px;
  }
  main :global(.judb-split > .splitpanes__splitter:hover) {
    background: var(--accent);
  }

  /* Full-page scrim + centred badge shown once the debuggee has exited. */
  .finished-overlay {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: grayscale(0.7) blur(1px);
    cursor: default;
  }
  .finished-badge {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.4rem;
    padding: 1.4rem 2.4rem;
    border-radius: 12px;
    background: var(--err-bg);
    color: var(--err-fg);
    border: 1px solid var(--err-fg);
    box-shadow: 0 8px 40px rgba(0, 0, 0, 0.5);
    text-align: center;
  }
  .finished-title {
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }
  .finished-sub {
    font-size: 0.85rem;
    opacity: 0.85;
  }
  .finished-hint {
    margin-top: 0.5rem;
    font-size: 0.75rem;
    opacity: 0.6;
  }
  .finished-overlay:focus {
    outline: none;
  }
</style>
