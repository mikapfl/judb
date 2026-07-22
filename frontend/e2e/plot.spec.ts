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

test("expand a variable to fetch its children lazily", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // `config` is a dict local; expanding it fetches one level of children.
    const config = page.locator(".vars .node", { hasText: "config" }).first();
    await config.locator(".row").first().click();

    // Its keys arrive (via an `expand` round-trip) as nested rows.
    await expect(config).toContainText("'scale'", { timeout: 10_000 });
    await expect(config).toContainText("'tags'");

    // Drill into the nested list; its indices show.
    const tags = config.locator(".node", { hasText: "'tags'" }).first();
    await tags.locator(".row").first().click();
    await expect(tags).toContainText("alpha", { timeout: 10_000 });
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("tab-completion offers names from the paused frame", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Type a prefix of a frame local and ask for completions with Tab.
    const cell = page.locator(".input .cm-content");
    await cell.click();
    await page.keyboard.type("sca");
    await page.keyboard.press("Tab");

    // The completer (run against the paused frame) offers `scale`.
    const tip = page.locator(".cm-tooltip-autocomplete");
    await expect(tip).toBeVisible({ timeout: 10_000 });
    await expect(tip).toContainText("scale");
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("clicking an outer stack frame retargets the variables pane", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Paused in `compute`: its local `scale` shows, `main`'s `label` does not.
    await expect(page.locator(".vars")).toContainText("scale");
    await expect(page.locator(".vars")).not.toContainText("label");

    // Select the `main` frame from the call stack.
    await page.locator(".stack button", { hasText: "main" }).click();

    // Variables retarget to `main`: `label` appears, `scale` is gone.
    await expect(page.locator(".vars")).toContainText("label");
    await expect(page.locator(".vars")).not.toContainText("scale");
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});
