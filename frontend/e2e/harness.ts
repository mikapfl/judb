import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..");

/** A private XDG home for one debuggee (or for a pair that must share one).
 *
 *  judb saves the working notebook and the watch list under `$XDG_STATE_HOME`,
 *  keyed by project root — which for every e2e debuggee is this checkout. Left
 *  to the real environment, tests running in parallel would restore each
 *  other's cells, and the suite would scribble in the developer's home. */
export function xdgRoot(): { root: string; state: string; config: string } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "judb-e2e-"));
  return { root, state: path.join(root, "state"), config: path.join(root, "config") };
}

/** Launch a Python debuggee (defaults to the live-pause fixture); resolve once
 *  it prints its server URL. Pass an `xdg` to share saved state between two
 *  spawns — the "restart and the cells are still there" test does exactly that. */
export function startDebuggee(
  script = "frontend/e2e/debuggee.py",
  xdg = xdgRoot(),
): Promise<{ proc: ChildProcessWithoutNullStreams; url: string }> {
  const proc = spawn("uv", ["run", "python", script], {
    cwd: REPO_ROOT,
    env: { ...process.env, XDG_STATE_HOME: xdg.state, XDG_CONFIG_HOME: xdg.config },
  });
  return new Promise((resolve, reject) => {
    let buf = "";
    const onData = (chunk: Buffer) => {
      buf += chunk.toString();
      const line = buf.split("\n").find((l) => l.startsWith("http://"));
      if (line) {
        proc.stdout.off("data", onData);
        resolve({ proc, url: line.trim() });
      }
    };
    proc.stdout.on("data", onData);
    proc.stderr.on("data", (c: Buffer) => process.stderr.write(c));
    proc.on("exit", (code) => reject(new Error(`debuggee exited early (${code})`)));
    setTimeout(() => reject(new Error("debuggee did not print a URL in time")), 20_000);
  });
}
