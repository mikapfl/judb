<script lang="ts">
  // The rightmost bottom pane: the exception behind a post-mortem pause (pytest
  // `--pdb`, or `-m judb` catching a crash). Empty when there is no exception —
  // an ordinary pause shows nothing here. Type + message sit at the top; the
  // exception chain fills the rest as a scrollable, Python-shaped traceback.
  //
  // The backend sends *structure*, never colour (judb/tracebacks.py): each frame
  // carries a window of raw source, which we highlight here with the very same
  // CodeMirror highlight style the Source pane uses. So the traceback is themed
  // by tokens.css alone — override the theme and this follows automatically.
  import { conn } from "../lib/connection.svelte";
  import { highlightLines } from "../lib/highlight";
  import type { TracebackFrame } from "../protocol";

  /** How Python introduces a chained exception. */
  const RELATION_TEXT = {
    cause: "The above exception was the direct cause of the following exception:",
    context: "During handling of the above exception, another exception occurred:",
  };

  /** The `^^^^` markers under the failing sub-expression (Python 3.11+ anchors),
   *  or null when there are none — or when they would underline the whole line,
   *  which is noise (Python omits them there too). */
  function anchor(frame: TracebackFrame, line: string): string | null {
    if (frame.col == null || frame.end_col == null) return null;
    const start = Math.max(0, frame.col);
    const end = Math.min(line.length, frame.end_col);
    if (end <= start) return null;
    const indent = line.length - line.trimStart().length;
    if (start <= indent && end >= line.trimEnd().length) return null;
    return " ".repeat(start) + "^".repeat(end - start);
  }

  /** One rendered source row: its number, its highlighted HTML, and whether it
   *  is the line that was executing (marked like the source pane's current line). */
  function rows(frame: TracebackFrame) {
    const html = highlightLines(frame.lines.join("\n"));
    return frame.lines.map((line, i) => {
      const lineno = frame.first_lineno + i;
      const current = lineno === frame.lineno;
      return {
        lineno,
        current,
        html: html[i] ?? "",
        anchor: current ? anchor(frame, line) : null,
      };
    });
  }

  const chain = $derived(conn.exception?.chain ?? []);

  /** Basename for display; the full path stays in the `title`. */
  const basename = (path: string) => path.split(/[\\/]/).pop() || path;

  /**
   * Where this traceback frame sits in the live call stack, or -1.
   *
   * A post-mortem stack *is* the raised exception's traceback, so its frames are
   * selectable: clicking one retargets source / variables / console, exactly as
   * the call-stack pane does. Frames from a chained *cause* have already unwound
   * and match nothing, so they simply stay unclickable. Matching on all three
   * fields (rather than trusting index alignment) keeps this honest whichever
   * entry of the chain a frame came from — the backend spells `filename` with
   * `Bdb.canonic`, the same as `stack`, so the comparison is exact.
   */
  function stackIndex(frame: TracebackFrame): number {
    return conn.stack.findIndex(
      (f) =>
        f.filename === frame.filename &&
        f.lineno === frame.lineno &&
        f.function === frame.function,
    );
  }
</script>

<!-- `file.py:12 in func`, the same shorthand the call stack uses; the full path
     stays in the tooltip, since the pane is narrow. -->
{#snippet location(frame: TracebackFrame)}
  <span class="file">{basename(frame.filename)}:{frame.lineno}</span>
  in <span class="fn">{frame.function}</span>
{/snippet}

<div class="exception">
  {#if conn.exception}
    <div class="exc-head">
      <span class="exc-type">{conn.exception.type}</span>
      <span class="exc-msg">{conn.exception.message}</span>
    </div>
    {#if chain.length}
      <div class="exc-tb">
        {#each chain as entry, i (i)}
          {#if entry.relation}
            <p class="relation">{RELATION_TEXT[entry.relation]}</p>
          {/if}
          {#if entry.frames.length}
            <p class="tb-intro">Traceback (most recent call last):</p>
          {/if}
          {#each entry.frames as frame, j (j)}
            {@const index = stackIndex(frame)}
            <div class="frame">
              <!-- A frame still on the stack is a button that selects it. One
                   that has already unwound (a chained cause) has no frame to
                   select, but its *file* is still there — so it opens that file
                   in the Source pane at the failing line. -->
              {#if index >= 0}
                <button
                  class="frame-loc selectable"
                  class:selected={index === conn.selected}
                  title={`${frame.filename} — click to select this frame`}
                  disabled={!conn.paused}
                  onclick={() => conn.selectFrame(index)}
                >
                  {@render location(frame)}
                </button>
              {:else}
                <button
                  class="frame-loc selectable"
                  title={`${frame.filename} — click to open this file (the frame has unwound)`}
                  disabled={!conn.paused}
                  onclick={() => conn.openFile(frame.filename, frame.lineno)}
                >
                  {@render location(frame)}
                </button>
              {/if}
              {#each rows(frame) as row (row.lineno)}
                <div class="row" class:current={row.current}>
                  <span class="gutter">{row.lineno}</span>
                  <!-- highlightLines escapes its input, so {@html} is safe. -->
                  <span class="src">{@html row.html}</span>
                </div>
                {#if row.anchor}
                  <div class="row anchor">
                    <span class="gutter"></span><span class="src">{row.anchor}</span>
                  </div>
                {/if}
              {/each}
            </div>
          {/each}
          <p class="headline">{entry.headline.join("\n")}</p>
        {/each}
      </div>
    {/if}
  {:else}
    <p class="empty">No exception.</p>
  {/if}
</div>

<style>
  .exception {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    color: var(--err-fg);
  }
  .exc-head {
    flex: none;
    padding: 0.4rem 0.5rem;
    border-bottom: 1px solid var(--err-fg);
    background: var(--err-bg);
  }
  .exc-type {
    font-weight: 700;
    font-family: var(--font-mono, monospace);
  }
  .exc-msg {
    margin-left: 0.4rem;
    opacity: 0.95;
    word-break: break-word;
  }
  .exc-tb {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 0.5rem;
    font-family: var(--font-mono, monospace);
    font-size: 0.8rem;
    line-height: 1.4;
    color: var(--fg);
  }
  .relation,
  .tb-intro {
    margin: 0.6rem 0 0.2rem;
    color: var(--fg-dim);
  }
  .relation {
    font-style: italic;
  }
  .frame {
    margin-bottom: 0.35rem;
  }
  /* A frame header is a `div` when the frame has unwound and a `button` when it
     is still selectable, so strip the button chrome and share one look. */
  .frame-loc {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0 0.2rem;
    border: none;
    border-radius: 3px;
    background: transparent;
    font: inherit;
    color: var(--fg-dim);
    cursor: default;
  }
  .frame-loc.selectable {
    cursor: pointer;
  }
  .frame-loc.selectable:hover:not(:disabled) {
    background: var(--bg-raised);
  }
  .frame-loc.selected {
    background: var(--accent-bg);
  }
  .frame-loc:disabled {
    opacity: 1; /* the global button rule dims disabled buttons; not here */
  }
  .frame-loc .file {
    color: var(--fg);
  }
  .frame-loc .fn {
    color: var(--accent);
  }
  .row {
    display: flex;
    white-space: pre;
  }
  .row.current {
    background: var(--accent-bg);
  }
  .gutter {
    flex: none;
    width: 4ch;
    padding-right: 0.6rem;
    text-align: right;
    color: var(--fg-faint);
    user-select: none;
  }
  /* Long lines scroll the pane rather than wrapping: a wrapped line would break
     the anchor markers' alignment with the code above them. */
  .src {
    flex: none;
  }
  .anchor .src {
    color: var(--err-fg);
  }
  .headline {
    margin: 0.2rem 0 0;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--err-fg);
    font-weight: 600;
  }
  .empty {
    margin: 0;
    padding: 0.5rem;
    color: var(--fg-dim);
    font-style: italic;
  }
</style>
