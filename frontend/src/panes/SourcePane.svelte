<script lang="ts">
  import { EditorState } from "@codemirror/state";
  import { EditorView } from "@codemirror/view";
  import {
    sourceExtensions,
    setCurrentLine,
    setMarkedLine,
    setBreakpoints,
  } from "../lib/codemirror";
  import { conn } from "../lib/connection.svelte";

  let host: HTMLDivElement;
  let view: EditorView | undefined;

  // The "open another file" input. Collapsed to a button until used; the
  // datalist offers the files judb already knows (stack / breakpoints /
  // traceback), while anything else is reachable by typing its path — which is
  // the case that matters, since a file you haven't reached is by definition
  // not in any of those lists.
  let opening = $state(false);
  let path = $state("");

  const basename = (p: string) => p.split(/[\\/]/).pop() || p;

  function openPath(): void {
    const wanted = path.trim();
    if (!wanted) return;
    conn.openFile(wanted);
    opening = false;
    path = "";
  }

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
    const source = conn.shownSource;
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
    const breaks = conn.shownBreakpoints;
    if (view) view.dispatch({ effects: setBreakpoints.of([...breaks]) });
  });

  /** Scroll `line` into view, if the document has it. */
  function reveal(line: number): void {
    if (!view || line < 1 || line > view.state.doc.lines) return;
    const pos = view.state.doc.line(line).from;
    view.dispatch({ effects: EditorView.scrollIntoView(pos, { y: "center" }) });
  }

  // The current line belongs to the paused frame only: while browsing another
  // file nothing is executing there, so the highlight is cleared (0) and the
  // navigated-to line gets its own, weaker marker instead.
  $effect(() => {
    const line = conn.browsing ? 0 : conn.lineno;
    if (!view || !conn.shownSource) return;
    view.dispatch({ effects: setCurrentLine.of(line) });
    reveal(line);
  });

  $effect(() => {
    const line = conn.markedLine;
    if (!view || !conn.shownSource) return;
    view.dispatch({ effects: setMarkedLine.of(line) });
    reveal(line);
  });

  $effect(() => () => view?.destroy());
</script>

<div class="source" role="presentation" oncontextmenu={onContextMenu}>
  <!-- Which file is on screen, and (while browsing) the way back. The gutter
       always sets breakpoints in *this* file, frame or not. -->
  <div class="filebar" class:browsing={conn.browsing}>
    <span class="name" title={conn.shownFilename}>
      {conn.shownFilename ? basename(conn.shownFilename) : "—"}
    </span>
    {#if conn.browsing}
      <span class="badge">not the paused frame</span>
      <button class="back" onclick={() => conn.backToFrame()}>Back to frame</button>
    {/if}
    {#if opening}
      <input
        class="path"
        list="judb-known-files"
        placeholder="path/to/file.py"
        aria-label="Open file"
        spellcheck="false"
        autocomplete="off"
        bind:value={path}
        {@attach (el) => el.focus()}
        onkeydown={(e) => {
          if (e.key === "Enter") openPath();
          else if (e.key === "Escape") {
            opening = false;
            path = "";
          }
        }}
      />
      <datalist id="judb-known-files">
        {#each conn.knownFiles as file (file)}
          <option value={file}></option>
        {/each}
      </datalist>
    {:else}
      <button class="open" onclick={() => (opening = true)}>Open file…</button>
    {/if}
  </div>

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
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }
  /* Thin bar above the editor: the file on screen + the open control. */
  .filebar {
    flex: none;
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.15rem 0.5rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.75rem;
    color: var(--fg-dim);
  }
  /* Browsing a file the debuggee is not stopped in — say so plainly, so the
     absence of a current-line highlight can't read as "it's not running". */
  .filebar.browsing {
    background: var(--warn-bg);
    color: var(--warn-fg);
  }
  .name {
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .badge {
    font-style: italic;
    white-space: nowrap;
  }
  .filebar button {
    margin-left: auto;
    padding: 0.05rem 0.4rem;
    border: 1px solid var(--border-strong);
    border-radius: 3px;
    background: var(--bg);
    color: var(--fg-dim);
    font-size: 0.75rem;
    cursor: pointer;
  }
  .filebar button.back {
    margin-left: 0;
  }
  .filebar button:hover {
    color: var(--fg);
    border-color: var(--accent);
  }
  .filebar .path {
    margin-left: auto;
    flex: 0 1 22rem;
    min-width: 8rem;
    padding: 0.1rem 0.3rem;
    border: 1px solid var(--accent);
    border-radius: 3px;
    background: var(--bg);
    color: var(--fg);
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }
  .filebar .path:focus {
    outline: none;
  }
  .host {
    flex: 1;
    min-height: 0;
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
