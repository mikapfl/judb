<script lang="ts">
  import { conn } from "./connection.svelte";
  import { theme } from "./theme.svelte";
  import PaneMenu from "./PaneMenu.svelte";
  import type { Command } from "../protocol";

  const send = (cmd: Command["cmd"]) => conn.send({ cmd } as Command);

  const statusLabel = $derived(
    conn.status === "running"
      ? "running…"
      : conn.status === "connecting"
        ? "connecting"
        : conn.status,
  );

  const themeIcon = $derived(
    theme.mode === "auto" ? "🌗" : theme.mode === "light" ? "☀️" : "🌙",
  );
  const themeTitle = $derived(
    theme.mode === "auto"
      ? `Theme: auto (${theme.resolved}) — click to force light`
      : theme.mode === "light"
        ? "Theme: light — click to force dark"
        : "Theme: dark — click to follow system",
  );
</script>

<header>
  <span class="status {conn.status}">{statusLabel}</span>
  <span class="loc" title={conn.location}>{conn.location}</span>
  <span class="spacer"></span>
  <!-- Each command carries its own `aria-label`, so the accessible name (and
       every test that finds a button by it) survives the narrow-window rule
       below hiding the visible text down to the glyph. -->
  <button aria-label="Continue" title="Continue" disabled={!conn.paused} onclick={() => send("continue")}>
    ▶<span class="label">Continue</span>
  </button>
  <button aria-label="Next" title="Next" disabled={!conn.paused} onclick={() => send("next")}>
    ⤼<span class="label">Next</span>
  </button>
  <button aria-label="Step" title="Step" disabled={!conn.paused} onclick={() => send("step")}>
    ↳<span class="label">Step</span>
  </button>
  <button aria-label="Return" title="Return" disabled={!conn.paused} onclick={() => send("return")}>
    ⇤<span class="label">Return</span>
  </button>
  <button aria-label="Quit" title="Quit" disabled={!conn.paused} onclick={() => send("quit")}>
    ■<span class="label">Quit</span>
  </button>
  <button
    class="interrupt"
    aria-label="Interrupt"
    title="Interrupt a running cell"
    disabled={!conn.busy}
    onclick={() => conn.interrupt()}
  >
    ✋<span class="label">Interrupt</span>
  </button>
  <PaneMenu />
  <button class="theme" title={themeTitle} aria-label={themeTitle} onclick={() => theme.cycle()}>
    {themeIcon}
  </button>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
  }
  /* Where the toolbar gives before the window does: the location truncates,
     then the button labels go. Nothing here may overflow horizontally — a
     scrolling <body> would slide the whole pane grid out from under the
     pointer. */
  .loc {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--fg-dim);
    margin-left: 0.25rem;
  }
  header button {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    white-space: nowrap;
  }
  @media (max-width: 860px) {
    header .label {
      display: none;
    }
  }
  .spacer {
    flex: 1;
  }
  .status {
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    background: var(--border);
    color: var(--status-fg);
  }
  .status.paused {
    background: var(--ok-bg);
    color: var(--ok-fg);
  }
  .status.running {
    background: var(--warn-bg);
    color: var(--warn-fg);
  }
  .status.finished,
  .status.disconnected {
    background: var(--err-bg);
    color: var(--err-fg);
  }
  /* Enabled only while a cell is running — the runaway-cell escape hatch. */
  .interrupt:not(:disabled) {
    color: var(--err-fg);
    border-color: var(--err-fg);
  }
  /* Compact icon toggle for auto/light/dark. */
  .theme {
    padding: 0.25rem 0.5rem;
    line-height: 1;
  }
</style>
