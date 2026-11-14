<script lang="ts">
  // The saved-cell library: the user's own cells and the project's committed
  // ones (docs/PHASE3A_PLAN.md §6). A popover rather than a pane — the layout
  // already reflows five secondary panes into up to three rows, and a sixth
  // would make that worse for something you reach for a few times a session.
  import { conn, matchesLocation, tabTitle } from "../lib/connection.svelte";
  import type { LibraryScope, SavedCell } from "../protocol";

  let { onclose }: { onclose: () => void } = $props();

  const scopes: { id: LibraryScope; label: string; note: string }[] = [
    { id: "project", label: "Project", note: "committed — anyone debugging here gets these" },
    { id: "user", label: "Yours", note: "every project; setup-tagged cells run once a session" },
  ];

  const here = (cell: SavedCell) => matchesLocation(cell.at, conn.locationKey);

  /** Cells that belong where we are now come first; the rest stay reachable,
   *  which is what makes a renamed function's orphaned cells findable. */
  function ordered(scope: LibraryScope): { cell: SavedCell; index: number }[] {
    const rows = conn.library[scope].map((cell, index) => ({ cell, index }));
    return [...rows.filter((r) => here(r.cell)), ...rows.filter((r) => !here(r.cell))];
  }

  const firstLine = (code: string) => code.split("\n")[0] ?? "";
</script>

<div class="library">
  <div class="head">
    <strong>Saved cells</strong>
    <button class="close" aria-label="Close" onclick={onclose}>×</button>
  </div>

  {#each scopes as scope (scope.id)}
    <section>
      <h4>
        {scope.label}
        <span class="note">{scope.note}</span>
      </h4>
      {#if conn.library[scope.id].length === 0}
        <p class="empty">Nothing saved yet.</p>
      {:else}
        <ul>
          {#each ordered(scope.id) as row (row.index)}
            <li class:matching={here(row.cell)}>
              <div class="what">
                <span class="name">{row.cell.name || firstLine(row.cell.code)}</span>
                <span class="at" title={row.cell.at || "available everywhere"}>
                  {row.cell.at ? tabTitle(row.cell.at) : "everywhere"}
                </span>
              </div>
              <div class="row-tools">
                <button onclick={() => conn.insertFromLibrary(row.cell)}>Insert</button>
                {#if conn.locationKey && !here(row.cell)}
                  <button
                    title="This cell's function was renamed or moved? Point it here."
                    onclick={() => conn.rekeyLibraryCell(scope.id, row.cell)}>Re-key</button
                  >
                {/if}
                <button
                  class="remove"
                  aria-label="Remove from library"
                  onclick={() => conn.removeFromLibrary(scope.id, row.cell)}>🗑</button
                >
              </div>
            </li>
          {/each}
        </ul>
      {/if}
      <div class="save">
        <button onclick={() => conn.saveTabTo(scope.id)}>
          Save “{tabTitle(conn.tabs[conn.active]?.at ?? "")}” here
        </button>
        <!-- Truncated from the left (`direction: rtl`), so the file name stays
             visible; the mark keeps the leading "/" from being reordered to the
             far end of the line, as a neutral character at the start would be. -->
        <code title={conn.library.paths[scope.id]}>&lrm;{conn.library.paths[scope.id]}</code>
      </div>
    </section>
  {/each}
</div>

<style>
  .library {
    position: absolute;
    right: 0.4rem;
    top: 1.9rem;
    z-index: 20;
    width: min(28rem, 92vw);
    max-height: 60vh;
    overflow: auto;
    padding: 0.5rem 0.6rem 0.6rem;
    background: var(--bg-inset);
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    font-size: 12px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.3rem;
  }
  .close {
    border: none;
    background: transparent;
    color: var(--fg-dim);
    font-size: 1.1rem;
    line-height: 1;
    cursor: pointer;
  }
  h4 {
    margin: 0.6rem 0 0.3rem;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--fg-dim);
  }
  .note {
    text-transform: none;
    letter-spacing: 0;
    color: var(--fg-faint);
    font-weight: 400;
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.2rem 0.3rem;
    border-radius: 4px;
  }
  li.matching {
    background: var(--bg);
  }
  .what {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
  }
  .at {
    flex: none;
    color: var(--fg-faint);
    font-size: 10px;
  }
  .row-tools {
    flex: none;
    display: flex;
    gap: 0.2rem;
  }
  button {
    padding: 0.1rem 0.4rem;
    font-size: 11px;
    background: var(--btn-bg);
    color: var(--btn-fg);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
  }
  button:hover {
    background: var(--btn-bg-hover);
  }
  .remove {
    background: transparent;
    border-color: transparent;
  }
  .empty {
    margin: 0.1rem 0.3rem;
    color: var(--fg-faint);
  }
  .save {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-top: 0.35rem;
  }
  .save code {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    direction: rtl;
    color: var(--fg-faint);
    font-size: 10px;
  }
</style>
