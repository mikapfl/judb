<script lang="ts">
  import { conn } from "./connection.svelte";
  import type { Command } from "../protocol";

  const send = (cmd: Command["cmd"]) => conn.send({ cmd } as Command);

  const statusLabel = $derived(
    conn.status === "running"
      ? "running…"
      : conn.status === "connecting"
        ? "connecting"
        : conn.status,
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
    color: #c8c8d0;
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
</style>
