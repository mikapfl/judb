<script lang="ts">
  import { Splitpanes, Pane } from "svelte-splitpanes";
  import Toolbar from "./lib/Toolbar.svelte";
  import PaneBox from "./lib/PaneBox.svelte";
  import SourcePane from "./panes/SourcePane.svelte";
  import ConsolePane from "./panes/ConsolePane.svelte";
  import VariablesPane from "./panes/VariablesPane.svelte";
  import StackPane from "./panes/StackPane.svelte";
  import { conn } from "./lib/connection.svelte";

  $effect(() => {
    conn.connect();
  });

  // The finished overlay is dismissable so the user can still inspect whatever
  // was last on screen. Reset the dismissal if a fresh session ever reconnects.
  let finishedDismissed = $state(false);
  $effect(() => {
    if (conn.status !== "finished") finishedDismissed = false;
  });
</script>

<Toolbar />

<main>
  <!-- Primary divider is top/bottom: the working area (Source + Console) gets
       most of the height; Variables + Call stack sit side by side underneath. -->
  <Splitpanes horizontal theme="" class="judb-split">
    <Pane size={80} minSize={30}>
      <Splitpanes theme="" class="judb-split">
        <Pane size={50} minSize={20}>
          <PaneBox title="Source">
            <SourcePane />
          </PaneBox>
        </Pane>
        <Pane size={50} minSize={20}>
          <PaneBox title="Notebook console — runs in the paused frame">
            <ConsolePane />
          </PaneBox>
        </Pane>
      </Splitpanes>
    </Pane>
    <Pane size={20} minSize={10}>
      <Splitpanes theme="" class="judb-split">
        <Pane size={45} minSize={15}>
          <PaneBox title="Call stack">
            <StackPane />
          </PaneBox>
        </Pane>
        <Pane size={55} minSize={15}>
          <PaneBox title="Variables">
            <VariablesPane />
          </PaneBox>
        </Pane>
      </Splitpanes>
    </Pane>
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
