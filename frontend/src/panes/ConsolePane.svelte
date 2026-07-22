<script lang="ts">
  import { EditorView, keymap } from "@codemirror/view";
  import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
  import { Prec } from "@codemirror/state";
  import { cellExtensions } from "../lib/codemirror";
  import { conn } from "../lib/connection.svelte";
  import Output from "../lib/Output.svelte";

  let host: HTMLDivElement;
  let historyEl: HTMLDivElement;
  let view: EditorView | undefined;

  function run() {
    if (!view || !conn.paused) return;
    const code = view.state.doc.toString();
    if (!code.trim()) return;
    conn.execute(code);
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "" } });
  }

  $effect(() => {
    const runKey = keymap.of([
      {
        key: "Mod-Enter",
        run: () => {
          run();
          return true;
        },
      },
    ]);
    view = new EditorView({
      parent: host,
      extensions: [
        Prec.highest(runKey),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        cellExtensions(),
      ],
    });
    return () => view?.destroy();
  });

  // Keep the newest cell / result in view.
  $effect(() => {
    conn.cells.length;
    conn.cells.at(-1)?.pending;
    if (historyEl) queueMicrotask(() => (historyEl.scrollTop = historyEl.scrollHeight));
  });
</script>

<div class="console">
  <div class="history" bind:this={historyEl}>
    {#each conn.cells as cell, i (i)}
      <div class="cell">
        <pre class="cell-code">{cell.code}</pre>
        {#if cell.pending}
          <div class="pending">running…</div>
        {:else}
          {#each cell.outputs as out, j (j)}
            <Output output={out} />
          {/each}
        {/if}
      </div>
    {/each}
  </div>
  <div class="input">
    <div class="editor" bind:this={host}></div>
    <div class="run-row">
      <button onclick={run} disabled={!conn.paused}>Run cell</button>
      <span class="hint">⌃/⌘+Enter · runs in the paused frame</span>
    </div>
  </div>
</div>

<style>
  .console {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  .history {
    flex: 1;
    overflow: auto;
    padding: 0.35rem 0.6rem;
    min-height: 0;
  }
  .cell {
    border-left: 2px solid var(--border);
    padding-left: 0.5rem;
    margin-bottom: 0.6rem;
  }
  .cell-code {
    margin: 0 0 0.2rem;
    color: var(--fg-dim);
    white-space: pre-wrap;
  }
  .pending {
    color: var(--warn-fg);
  }
  .input {
    border-top: 1px solid var(--border);
    background: var(--bg-inset);
  }
  .editor {
    max-height: 10rem;
    overflow: auto;
    padding: 0.35rem 0.6rem;
  }
  .run-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.35rem 0.6rem;
  }
  .hint {
    color: var(--fg-faint);
    font-size: 11px;
  }
</style>
