<script lang="ts">
  import { conn } from "../lib/connection.svelte";
  import Output from "../lib/Output.svelte";

  // Which rows have their rich repr unfolded, keyed by expression (so a row
  // keeps its state across re-evaluation, and a deleted one drops out).
  let open = $state<string[]>([]);

  const isOpen = (expr: string) => open.includes(expr);

  function toggle(expr: string) {
    open = isOpen(expr) ? open.filter((e) => e !== expr) : [...open, expr];
  }

  let draft = $state("");

  function addDraft() {
    conn.addWatch(draft);
    draft = "";
  }

  // Commit an edited row on Enter / blur; Escape restores the current value by
  // simply re-reading it from the store.
  function commit(index: number, el: HTMLInputElement) {
    conn.setWatch(index, el.value);
  }
</script>

<div class="watch">
  <ul>
    {#each conn.watches as expr, i (expr)}
      {@const value = conn.watchValue(i)}
      <li class="row">
        <!-- Every evaluated watch unfolds: the row summary is one truncated
             line, the repr is the whole value (a DataFrame's HTML table, a
             figure, or simply the untruncated text). -->
        {#if value?.repr}
          <button
            class="twist"
            aria-label={isOpen(expr) ? "Hide value" : "Show value"}
            onclick={() => toggle(expr)}
          >
            {isOpen(expr) ? "▾" : "▸"}
          </button>
        {:else}
          <span class="twist"></span>
        {/if}

        <input
          class="expr"
          value={expr}
          aria-label="Watch expression"
          spellcheck="false"
          onkeydown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            else if (e.key === "Escape") {
              e.currentTarget.value = expr;
              e.currentTarget.blur();
            }
          }}
          onblur={(e) => commit(i, e.currentTarget)}
        />

        {#if value?.error}
          <span class="err" title={value.error}>{value.error}</span>
        {:else if value}
          <span class="summary" title={value.summary}>{value.summary}</span>
        {:else}
          <span class="pending">…</span>
        {/if}

        <button
          class="remove"
          title="Remove watch"
          aria-label="Remove watch"
          onclick={() => conn.removeWatch(i)}
        >
          ×
        </button>
      </li>

      {#if isOpen(expr) && value?.repr}
        <li class="repr">
          <Output output={{ kind: "display_data", data: value.repr }} />
        </li>
      {/if}
    {/each}
  </ul>

  <!-- Watches are evaluated in the selected frame; adding one while the
       debuggee runs is fine, it is answered at the next pause. -->
  <input
    class="add"
    placeholder="+ expression"
    aria-label="Add watch expression"
    spellcheck="false"
    bind:value={draft}
    onkeydown={(e) => {
      if (e.key === "Enter") addDraft();
      else if (e.key === "Escape") draft = "";
    }}
    onblur={addDraft}
  />
</div>

<style>
  .watch {
    height: 100%;
    overflow: auto;
    padding: 0.35rem 0.6rem;
    font-family: var(--font-mono);
    font-size: 12px;
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .row {
    display: flex;
    align-items: baseline;
    gap: 0.35rem;
    border-radius: 3px;
  }
  .row:hover {
    background: var(--accent-bg);
  }
  .twist {
    flex: 0 0 auto;
    width: 1ch;
    padding: 0;
    border: none;
    background: none;
    color: var(--fg-faint);
    font: inherit;
    cursor: pointer;
  }
  /* The expression is directly editable in place — no edit mode to enter. */
  .expr {
    flex: 0 1 auto;
    width: 12ch;
    min-width: 4ch;
    padding: 0.1rem 0.15rem;
    border: none;
    border-bottom: 1px solid transparent;
    background: none;
    color: var(--fg);
    font: inherit;
  }
  .expr:hover {
    border-bottom-color: var(--border);
  }
  .expr:focus {
    outline: none;
    border-bottom-color: var(--accent);
    flex-basis: 100%;
  }
  .summary,
  .err,
  .pending {
    flex: 1 1 auto;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .summary {
    color: var(--fg-faint);
  }
  .err {
    color: var(--err-fg);
  }
  .pending {
    color: var(--fg-faint);
  }
  .remove {
    flex: none;
    margin-left: auto;
    padding: 0 0.3rem;
    border: none;
    background: transparent;
    color: var(--fg-faint);
    font-size: 1rem;
    line-height: 1;
    cursor: pointer;
    border-radius: 3px;
  }
  .remove:hover {
    color: var(--err-fg);
    background: var(--bg);
  }
  .repr {
    margin: 0.1rem 0 0.3rem 1ch;
    border-left: 1px solid var(--border);
    padding-left: 0.4rem;
  }
  .add {
    width: 100%;
    margin-top: 0.2rem;
    padding: 0.15rem;
    border: none;
    border-top: 1px solid var(--border);
    background: none;
    color: var(--fg);
    font: inherit;
  }
  .add::placeholder {
    color: var(--fg-faint);
  }
  .add:focus {
    outline: none;
    border-top-color: var(--accent);
  }
</style>
