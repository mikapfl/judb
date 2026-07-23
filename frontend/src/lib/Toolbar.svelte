<script lang="ts">
  import { conn } from "./connection.svelte";
  import { theme } from "./theme.svelte";
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
  <span class="loc">{conn.location}</span>
  <span class="spacer"></span>
  <button disabled={!conn.paused} onclick={() => send("continue")}>▶ Continue</button>
  <button disabled={!conn.paused} onclick={() => send("next")}>⤼ Next</button>
  <button disabled={!conn.paused} onclick={() => send("step")}>↳ Step</button>
  <button disabled={!conn.paused} onclick={() => send("return")}>⇤ Return</button>
  <button disabled={!conn.paused} onclick={() => send("quit")}>■ Quit</button>
  <button class="interrupt" disabled={!conn.busy} onclick={() => conn.interrupt()}>
    ✋ Interrupt
  </button>
  <button class="theme" title={themeTitle} aria-label={themeTitle} onclick={() => theme.cycle()}>
    {themeIcon}
  </button>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
  }
  .loc {
    color: var(--fg-dim);
    margin-left: 0.25rem;
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
