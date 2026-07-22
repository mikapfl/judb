import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..");

/** Launch the Python debuggee; resolve once it prints its server URL. */
function startDebuggee(): Promise<{ proc: ChildProcessWithoutNullStreams; url: string }> {
  const proc = spawn("uv", ["run", "python", "frontend/e2e/debuggee.py"], {
    cwd: REPO_ROOT,
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

test("plot a paused frame's array in the browser, then continue", async ({ page }) => {
  const { proc, url } = await startDebuggee();
  const exited = new Promise<void>((r) => proc.on("exit", () => r()));

  try {
    await page.goto(url);

    // Paused: status pill flips and the source shows the debuggee.
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(page.locator(".source")).toContainText("np.linspace");
    await expect(page.locator(".vars")).toContainText("data");

    // Type a plot cell into the console editor (the editable CodeMirror) and run.
    const cell = page.locator(".input .cm-content");
    await cell.click();
    await page.keyboard.type("import matplotlib.pyplot as plt; plt.plot(data)");
    await page.getByRole("button", { name: "Run cell" }).click();

    // The paused frame's `data` renders as an inline PNG.
    const img = page.locator(".history img");
    await expect(img).toBeVisible({ timeout: 15_000 });
    await expect(img).toHaveAttribute("src", /^data:image\/png;base64,/);

    // Continue → the debuggee runs to completion and the process exits.
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.locator(".status")).toHaveText(/finished|disconnected/, {
      timeout: 15_000,
    });
    await exited;
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});
