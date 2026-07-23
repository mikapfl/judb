<script lang="ts">
  import { untrack } from "svelte";
  import { EditorView, keymap } from "@codemirror/view";
  import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
  import { Prec } from "@codemirror/state";
  import type { CompletionContext, CompletionResult } from "@codemirror/autocomplete";
  import { cellExtensions } from "../lib/codemirror";
  import { conn, type Cell } from "../lib/connection.svelte";
  import Output from "../lib/Output.svelte";

  let {
    cell,
    first,
    last,
    onrun,
    onrunAdvance,
    ondelete,
    onmoveUp,
    onmoveDown,
    onaddBelow,
  }: {
    cell: Cell;
    first: boolean;
    last: boolean;
    onrun: (code: string) => void;
    onrunAdvance: (code: string) => void;
    ondelete: () => void;
    onmoveUp: () => void;
    onmoveDown: () => void;
    onaddBelow: () => void;
  } = $props();

  let host: HTMLDivElement;
  let view: EditorView | undefined;

  /** Focus this cell's editor (called by the pane after add / run-and-advance). */
  export function focus(): void {
    view?.focus();
  }

  const code = () => view?.state.doc.toString() ?? "";

  // Ask the paused frame's IPython completer for matches around the cursor.
  async function completions(
    context: CompletionContext,
  ): Promise<CompletionResult | null> {
    const before = context.matchBefore(/[\w.]+$/);
    if (!context.explicit && !before) return null;
    const res = await conn.complete(context.state.doc.toString(), context.pos);
    if (!res.matches.length) return null;
    return { from: res.from, options: res.matches.map((label) => ({ label })) };
  }

  // Build the editor once per cell instance. `cell.code` is read untracked (and
  // the run handlers read props lazily, at keypress) so parent re-renders never
  // recreate the editor — the keyed {#each} already gives each cell a stable
  // editor that survives reorder/insert/delete.
  $effect(() => {
    const runKeys = keymap.of([
      { key: "Mod-Enter", run: () => (onrun(code()), true) },
      { key: "Shift-Enter", run: () => (onrunAdvance(code()), true) },
    ]);
    view = new EditorView({
      parent: host,
      doc: untrack(() => cell.code),
      extensions: [
        Prec.highest(runKeys),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        cellExtensions(completions),
      ],
    });
    return () => view?.destroy();
  });

  const label = $derived(cell.pending ? "[*]" : cell.count == null ? "[ ]" : `[${cell.count}]`);
</script>

<div class="cell" class:pending={cell.pending}>
  <div class="cell-body">
    <div class="gutter">
      <span class="count" title="execution order">{label}</span>
    </div>
    <div class="editor" bind:this={host}></div>
    <div class="tools">
      <button
        class="run"
        aria-label="Run cell"
        title="Run cell (⌘/⌃+Enter)"
        disabled={!conn.paused}
        onclick={() => onrun(code())}>▶</button
      >
      <button aria-label="Move cell up" title="Move up" disabled={first} onclick={onmoveUp}
        >↑</button
      >
      <button aria-label="Move cell down" title="Move down" disabled={last} onclick={onmoveDown}
        >↓</button
      >
      <button aria-label="Add cell below" title="Add cell below" onclick={onaddBelow}>＋</button>
      <button aria-label="Delete cell" title="Delete cell" onclick={ondelete}>🗑</button>
    </div>
  </div>

  {#if cell.pending}
    <div class="outputs"><div class="pending">running…</div></div>
  {:else if cell.outputs.length}
    <div class="outputs">
      {#each cell.outputs as out, j (j)}
        <Output output={out} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .cell {
    margin: 0 0.5rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-inset);
    overflow: hidden;
  }
  .cell.pending {
    border-color: var(--warn-fg);
  }
  .cell-body {
    display: flex;
    align-items: flex-start;
  }
  .gutter {
    flex: 0 0 auto;
    padding: 0.4rem 0.2rem 0.4rem 0.4rem;
    color: var(--fg-faint);
    font-family: var(--font-mono);
    font-size: 11px;
    user-select: none;
  }
  .editor {
    flex: 1 1 auto;
    min-width: 0;
    max-height: 14rem;
    overflow: auto;
    padding: 0.3rem 0.2rem;
  }
  .tools {
    flex: 0 0 auto;
    display: flex;
    gap: 0.15rem;
    padding: 0.35rem 0.4rem;
    opacity: 0.35;
    transition: opacity 0.12s;
  }
  .cell:hover .tools,
  .cell:focus-within .tools {
    opacity: 1;
  }
  .tools button {
    padding: 0.1rem 0.3rem;
    font-size: 12px;
    line-height: 1.2;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    cursor: pointer;
    color: var(--fg-dim);
  }
  .tools button:hover:not(:disabled) {
    background: var(--btn-bg-hover);
    color: var(--fg);
  }
  .tools button:disabled {
    opacity: 0.3;
    cursor: default;
  }
  .tools .run:not(:disabled) {
    color: var(--ok-fg);
  }
  .outputs {
    border-top: 1px solid var(--border);
    padding: 0.2rem 0.6rem;
    background: var(--bg);
  }
  .pending {
    color: var(--warn-fg);
    padding: 0.2rem 0;
  }
</style>
