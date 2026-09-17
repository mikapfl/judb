<script lang="ts">
  import { tick } from "svelte";
  import { conn, tabTitle } from "../lib/connection.svelte";
  import NotebookCell from "./NotebookCell.svelte";
  import CellLibrary from "./CellLibrary.svelte";

  let libraryOpen = $state(false);

  // Where a pin would put a cell: the selected frame's location key. Empty for
  // code with no file on disk, and pointless when this tab is already there.
  const pinTarget = $derived(
    conn.locationKey && conn.tabs[conn.active]?.at !== conn.locationKey ? conn.locationKey : "",
  );

  // Component instances by cell id, so we can focus a specific cell's editor
  // after inserting one or running-and-advancing (Jupyter's Shift+Enter).
  let refs = $state<Record<number, NotebookCell | undefined>>({});

  async function focusCell(id: number): Promise<void> {
    await tick();
    refs[id]?.focus();
  }

  function runAdvance(id: number, code: string): void {
    conn.runCell(id, code);
    if (!conn.paused) return;
    const i = conn.cells.findIndex((c) => c.id === id);
    // Move to the next cell, creating one if this was the last (like Jupyter).
    const nextId = i === conn.cells.length - 1 ? conn.addCell(id) : conn.cells[i + 1].id;
    void focusCell(nextId);
  }

  function addBelow(id: number): void {
    void focusCell(conn.addCell(id));
  }

  function addAtEnd(): void {
    void focusCell(conn.addCell());
  }
</script>

<div class="notebook">
  <!-- One tab per location that has cells, Scratch first. A tab appears only
       once something is pinned to it, so stepping through code you have no
       cells for never moves the console (docs/PHASE3A_PLAN.md §4). -->
  <div class="tabstrip">
    <div class="tabs" role="tablist">
      {#each conn.tabs as tab, i (tab.at)}
        <div class="tab" class:active={i === conn.active} class:pending={conn.pendingTab === i}>
          <button
            role="tab"
            aria-selected={i === conn.active}
            title={tab.at || "Cells available everywhere"}
            onclick={() => conn.selectTab(i)}
          >
            {tabTitle(tab.at)}
            {#if conn.pendingTab === i}<span class="dot" title="the paused frame is here">●</span
              >{/if}
          </button>
          {#if i > 0}
            <button
              class="shut"
              aria-label="Close tab {tabTitle(tab.at)}"
              title="Discard these cells"
              onclick={() => conn.closeTab(i)}>×</button
            >
          {/if}
        </div>
      {/each}
    </div>
    <button
      class="library-toggle"
      aria-expanded={libraryOpen}
      title="Saved cells: yours and this project's"
      onclick={() => (libraryOpen = !libraryOpen)}>☰ Saved cells</button
    >
    {#if libraryOpen}
      <CellLibrary onclose={() => (libraryOpen = false)} />
    {/if}
  </div>
  <div class="cells">
    {#each conn.cells as cell, i (cell.id)}
      <NotebookCell
        bind:this={refs[cell.id]}
        {cell}
        first={i === 0}
        last={i === conn.cells.length - 1}
        onrun={(code) => conn.runCell(cell.id, code)}
        onrunAdvance={(code) => runAdvance(cell.id, code)}
        ondelete={() => conn.deleteCell(cell.id)}
        onmoveUp={() => conn.moveCell(cell.id, -1)}
        onmoveDown={() => conn.moveCell(cell.id, 1)}
        onaddBelow={() => addBelow(cell.id)}
        onedit={(code) => conn.editCell(cell.id, code)}
        onpin={pinTarget ? () => conn.pinCell(cell.id, pinTarget) : undefined}
        pinLabel={pinTarget ? `Pin this cell to ${tabTitle(pinTarget)}` : ""}
      />
    {/each}
  </div>
  <div class="footer">
    <button class="add-cell" onclick={addAtEnd}>＋ Add cell</button>
    {#if pinTarget}
      <button class="pin-tab" title={`Pin every cell here to ${pinTarget}`} onclick={() => conn.pinTab(pinTarget)}>
        📌 Pin tab to {tabTitle(pinTarget)}
      </button>
    {/if}
    <span class="hint">⌘/⌃+Enter run · ⇧+Enter run &amp; next · runs in the paused frame</span>
  </div>
</div>

<style>
  .notebook {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  .tabstrip {
    position: relative;
    display: flex;
    align-items: stretch;
    gap: 0.4rem;
    padding: 0.2rem 0.4rem 0;
    border-bottom: 1px solid var(--border);
    background: var(--bg-inset);
  }
  .tabs {
    flex: 1;
    min-width: 0;
    display: flex;
    gap: 0.15rem;
    overflow-x: auto;
  }
  .tab {
    display: flex;
    align-items: center;
    border: 1px solid transparent;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
  }
  .tab.active {
    background: var(--bg);
    border-color: var(--border);
  }
  .tab.pending {
    border-color: var(--accent);
  }
  .tab > button {
    padding: 0.2rem 0.5rem;
    max-width: 14rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    background: transparent;
    border: none;
    color: var(--fg-dim);
    cursor: pointer;
  }
  .tab.active > button {
    color: var(--fg);
  }
  .tab .shut {
    padding: 0 0.3rem;
    font-size: 12px;
    color: var(--fg-faint);
  }
  .dot {
    color: var(--accent);
    font-size: 9px;
    vertical-align: middle;
  }
  .library-toggle,
  .pin-tab {
    flex: none;
    align-self: center;
    padding: 0.15rem 0.5rem;
    margin-bottom: 0.2rem;
    font-size: 11px;
    background: var(--btn-bg);
    color: var(--btn-fg);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
  }
  .library-toggle:hover,
  .pin-tab:hover {
    background: var(--btn-bg-hover);
  }
  .pin-tab {
    margin-bottom: 0;
  }
  .cells {
    flex: 1;
    overflow: auto;
    padding: 0.5rem 0 0.2rem;
    min-height: 0;
  }
  .footer {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.35rem 0.6rem;
    border-top: 1px solid var(--border);
    background: var(--bg-inset);
  }
  .add-cell {
    padding: 0.2rem 0.6rem;
    font-size: 12px;
    background: var(--btn-bg);
    color: var(--btn-fg);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
  }
  .add-cell:hover {
    background: var(--btn-bg-hover);
  }
  .hint {
    color: var(--fg-faint);
    font-size: 11px;
  }
</style>
