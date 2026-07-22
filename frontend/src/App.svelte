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
</script>

<Toolbar />

<main>
  <Splitpanes theme="" class="judb-split">
    <Pane size={62} minSize={25}>
      <Splitpanes horizontal theme="" class="judb-split">
        <Pane size={60} minSize={15}>
          <PaneBox title="Source">
            <SourcePane />
          </PaneBox>
        </Pane>
        <Pane size={40} minSize={15}>
          <PaneBox title="Console — runs in the paused frame">
            <ConsolePane />
          </PaneBox>
        </Pane>
      </Splitpanes>
    </Pane>
    <Pane size={38} minSize={15}>
      <Splitpanes horizontal theme="" class="judb-split">
        <Pane size={55} minSize={10}>
          <PaneBox title="Variables">
            <VariablesPane />
          </PaneBox>
        </Pane>
        <Pane size={45} minSize={10}>
          <PaneBox title="Call stack">
            <StackPane />
          </PaneBox>
        </Pane>
      </Splitpanes>
    </Pane>
  </Splitpanes>
</main>

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
</style>
