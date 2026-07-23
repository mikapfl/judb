<script lang="ts">
  import { tick } from "svelte";
  import { conn } from "../lib/connection.svelte";
  import NotebookCell from "./NotebookCell.svelte";

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
      />
    {/each}
  </div>
  <div class="footer">
    <button class="add-cell" onclick={addAtEnd}>＋ Add cell</button>
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
