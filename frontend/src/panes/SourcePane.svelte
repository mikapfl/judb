<script lang="ts">
  import { EditorState } from "@codemirror/state";
  import { EditorView } from "@codemirror/view";
  import { sourceExtensions, setCurrentLine, setBreakpoints } from "../lib/codemirror";
  import { conn } from "../lib/connection.svelte";

  let host: HTMLDivElement;
  let view: EditorView | undefined;

  // The condition editor popover: which line, and where to anchor it. A right
  // click on the gutter opens it (see `sourceExtensions`); Save/Remove/Cancel or
  // an outside click closes it.
  let editor = $state<{ line: number; top: number; left: number } | null>(null);
  let cond = $state("");
  let temporary = $state(false);
  let ignore = $state(0);

  function openEditor(line: number, rect: DOMRect): void {
    const bp = conn.breakpointAt(line);
    cond = bp?.cond ?? "";
    temporary = bp?.temporary ?? false;
    ignore = bp?.ignore ?? 0;
    editor = { line, top: rect.bottom + 2, left: rect.right + 4 };
  }

  function save(): void {
    if (!editor) return;
    conn.setBreak(editor.line, { cond, temporary, ignore });
    editor = null;
  }

  function remove(): void {
    if (!editor) return;
    conn.clearBreak(editor.line);
    editor = null;
  }

  const extensions = () => sourceExtensions((line) => conn.toggleBreak(line));

  // A right click on the breakpoint gutter opens the condition editor instead of
  // the native browser menu. Handling it here (rather than in the CM gutter)
  // lets us `preventDefault` before the popover/backdrop render — while the
  // event target is still the gutter — so the cancel reliably sticks (a CM
  // gutter handler and a target-scoped one both leaked the menu in Firefox, the
  // latter because the freshly-rendered backdrop became the event's target).
  function onContextMenu(e: MouseEvent): void {
    const target = e.target as HTMLElement;
    if (!view || !target.closest(".cm-breakpoint-gutter")) return;
    e.preventDefault();
    const block = view.lineBlockAtHeight(e.clientY - view.documentTop);
    const line = view.state.doc.lineAt(block.from).number;
    openEditor(line, new DOMRect(e.clientX, e.clientY, 0, 0));
  }

  // Recreate the document when the source text changes (a new frame/file);
  // move the current-line highlight + scroll on every pause.
  $effect(() => {
    const source = conn.source;
    if (!view) {
      view = new EditorView({ parent: host, doc: source, extensions: extensions() });
    } else if (source !== view.state.doc.toString()) {
      view.setState(EditorState.create({ doc: source, extensions: extensions() }));
    }
  });

  // Redraw the breakpoint gutter whenever the set changes (toggle reply, or a
  // new frame carrying its file's breakpoints). Runs after the doc effect, so
  // the freshly-created state is the one we dispatch into.
  $effect(() => {
    const breaks = conn.breakpoints;
    if (view) view.dispatch({ effects: setBreakpoints.of([...breaks]) });
  });

  $effect(() => {
    const line = conn.lineno;
    if (!view || !conn.source) return;
    view.dispatch({ effects: setCurrentLine.of(line) });
    if (line >= 1 && line <= view.state.doc.lines) {
      const pos = view.state.doc.line(line).from;
      view.dispatch({ effects: EditorView.scrollIntoView(pos, { y: "center" }) });
    }
  });

  $effect(() => () => view?.destroy());
</script>

<div class="source" role="presentation" oncontextmenu={onContextMenu}>
  <div class="host" bind:this={host}></div>

  {#if editor}
    <!-- Click-away backdrop closes the popover without dispatching; a right
         click also just dismisses it (no native menu). -->
    <div
      class="backdrop"
      role="presentation"
      onmousedown={() => (editor = null)}
      oncontextmenu={(e) => e.preventDefault()}
    ></div>
    <div
      class="popover"
      role="dialog"
      aria-label="Edit breakpoint"
      style="top: {editor.top}px; left: {editor.left}px"
    >
      <label>
        Condition
        <input
          type="text"
          placeholder="e.g. i == 3"
          bind:value={cond}
          spellcheck="false"
          autocomplete="off"
          onkeydown={(e) => {
            if (e.key === "Enter") save();
            else if (e.key === "Escape") editor = null;
          }}
        />
      </label>
      <label class="row">
        <input type="checkbox" bind:checked={temporary} />
        Temporary (clear after first hit)
      </label>
      <label class="row">
        Ignore first
        <input class="ignore" type="number" min="0" bind:value={ignore} />
        hits
      </label>
      <div class="actions">
        <button class="primary" onclick={save}>Save</button>
        {#if conn.breakpointAt(editor.line)}
          <button onclick={remove}>Remove</button>
        {/if}
        <button onclick={() => (editor = null)}>Cancel</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .source {
    position: relative;
    height: 100%;
    overflow: hidden;
  }
  .host {
    height: 100%;
  }
  .host :global(.cm-editor) {
    height: 100%;
  }
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 10;
  }
  .popover {
    position: fixed;
    z-index: 11;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 15rem;
    padding: 0.6rem;
    background: var(--bg-raised);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
    font-size: 0.85rem;
  }
  .popover label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    color: var(--fg-faint);
  }
  .popover label.row {
    flex-direction: row;
    align-items: center;
    gap: 0.4rem;
    color: var(--fg);
  }
  .popover input[type="text"],
  .popover input[type="number"] {
    padding: 0.25rem 0.4rem;
    background: var(--bg);
    color: var(--fg);
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    font-family: var(--font-mono);
  }
  .popover input.ignore {
    width: 4rem;
  }
  .actions {
    display: flex;
    gap: 0.4rem;
    justify-content: flex-end;
  }
  .actions button {
    padding: 0.25rem 0.6rem;
    background: var(--bg);
    color: var(--fg);
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    cursor: pointer;
  }
  .actions button.primary {
    background: var(--accent);
    color: var(--bg);
    border-color: var(--accent);
  }
</style>
