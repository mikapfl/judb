<script lang="ts">
  import { EditorState } from "@codemirror/state";
  import { EditorView } from "@codemirror/view";
  import { sourceExtensions, setCurrentLine, setBreakpoints } from "../lib/codemirror";
  import { conn } from "../lib/connection.svelte";

  let host: HTMLDivElement;
  let view: EditorView | undefined;

  const extensions = () => sourceExtensions((line) => conn.toggleBreak(line));

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
    const lines = conn.breakpoints;
    if (view) view.dispatch({ effects: setBreakpoints.of([...lines]) });
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

<div class="source" bind:this={host}></div>

<style>
  .source {
    height: 100%;
    overflow: hidden;
  }
  .source :global(.cm-editor) {
    height: 100%;
  }
</style>
