<script lang="ts">
  import { conn } from "./connection.svelte";
  import { theme } from "./theme.svelte";
  import { loadMplClient, type MplFigure, type MplSocket } from "./mplClient";
  import type { MplMsg } from "../protocol";

  // An interactive matplotlib figure (WebAgg) bound to backend figure `id`.
  // matplotlib's mpl.js builds the canvas + toolbar inside `host`; we bridge its
  // websocket to judb's (events out via conn.sendMplEvent, frames in via the
  // store's per-figure handler). See judb/mpl_backend.py.
  let { id }: { id: string } = $props();

  let host: HTMLDivElement;
  let error = $state("");

  $effect(() => {
    let disposed = false;
    let unregister: (() => void) | undefined;
    let socket: MplSocket | undefined;

    loadMplClient()
      .then((mpl) => {
        if (disposed) return;

        // A websocket-like shim: mpl.js drives `.send()`/`.onmessage`; we route
        // those over judb's command/message channel, keyed by figure id.
        socket = {
          binaryType: "arraybuffer", // advertise binary (diff-image) support
          readyState: 1, // WebSocket.OPEN — see MplSocket
          onopen: () => {},
          onmessage: () => {},
          send: (payload: string) => conn.sendMplEvent(id, JSON.parse(payload)),
          close: () => {},
        };

        const ondownload = (f: MplFigure) => {
          const a = document.createElement("a");
          a.href = f.canvas.toDataURL();
          a.download = `figure-${id}.png`;
          a.click();
        };

        new mpl.figure(id, socket, ondownload, host);

        // Frames/control messages from the backend → the figure's onmessage.
        // A base64 PNG is delivered as a data: URI string (mpl.js accepts that
        // directly); JSON control messages as their serialised text.
        unregister = conn.registerMpl(id, (msg: MplMsg) => {
          if (msg.blob !== undefined)
            socket!.onmessage({ data: `data:image/png;base64,${msg.blob}` });
          else socket!.onmessage({ data: JSON.stringify(msg.json) });
        });

        // mpl.js assumes a real (already-open) socket, so fire onopen now — this
        // sends supports_binary / refresh, kicking off the first render.
        socket.onopen();
      })
      .catch((e: unknown) => {
        if (!disposed) error = e instanceof Error ? e.message : String(e);
      });

    return () => {
      disposed = true;
      unregister?.();
      socket?.close();
    };
  });
</script>

<div class="webagg-host" class:dark={theme.resolved === "dark"} bind:this={host}>
  {#if error}
    <div class="webagg-error">interactive figure failed to load: {error}</div>
  {/if}
</div>

<style>
  .webagg-host {
    margin: 0.4rem 0;
    max-width: 100%;
    overflow: auto;
  }
  .webagg-error {
    color: var(--err-fg);
    font-size: 12px;
    padding: 0.4rem;
  }
  /* matplotlib builds the figure DOM itself, so these must be :global. */
  .webagg-host :global(canvas) {
    background: #ffffff; /* figures are always light, like our inline PNGs */
  }
  .webagg-host :global(.ui-dialog-titlebar) {
    display: none; /* the "Figure N" titlebar adds little here */
  }
  .webagg-host :global(.mpl-toolbar) {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem;
    padding: 0.3rem 0;
  }
  .webagg-host :global(.mpl-button-group) {
    display: inline-flex;
    gap: 0.15rem;
  }
  .webagg-host :global(.mpl-widget) {
    display: inline-flex;
    align-items: center;
    padding: 0.15rem 0.3rem;
    font-size: 12px;
    background: var(--btn-bg);
    color: var(--btn-fg);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
  }
  .webagg-host :global(.mpl-widget:hover) {
    background: var(--btn-bg-hover);
  }
  .webagg-host :global(.mpl-widget:disabled) {
    opacity: 0.4;
    cursor: default;
  }
  /* matplotlib's toolbar icons (served from /_images) are black glyphs — invert
     them in dark mode so they read as light on the dark buttons. */
  .webagg-host :global(.mpl-widget img) {
    width: 16px;
    height: 16px;
    display: block;
  }
  .webagg-host.dark :global(.mpl-widget img) {
    filter: invert(1);
  }
  .webagg-host :global(.mpl-message) {
    color: var(--fg-dim);
    font-family: var(--font-mono);
    font-size: 11px;
    margin-left: 0.4rem;
  }
</style>
