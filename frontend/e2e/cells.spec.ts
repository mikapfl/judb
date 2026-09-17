import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { startDebuggee, xdgRoot } from "./harness";

// Phase 3a in a real browser: cells that outlive the debuggee, tabs that follow
// the selected frame, and the user's own cell library.
//
// Nothing here saves to the *project* library — that would put a file in this
// checkout; the Python suite covers that path (tests/test_cells.py).

async function typeInFirstCell(page: Page, code: string): Promise<void> {
  const cell = page.locator(".cell .cm-content").first();
  await cell.click();
  await page.keyboard.type(code);
}

test("the notebook outlives the debuggee process", async ({ page }) => {
  const xdg = xdgRoot();
  const first = await startDebuggee("frontend/e2e/debuggee.py", xdg);
  try {
    await page.goto(first.url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await typeInFirstCell(page, "scale * 21");
    // The save is debounced; the file is what the next run reads.
    await expect
      .poll(
        () => {
          const dir = path.join(xdg.state, "judb", "notebooks");
          if (!fs.existsSync(dir)) return "";
          return fs
            .readdirSync(dir)
            .filter((f) => f.endsWith(".py"))
            .map((f) => fs.readFileSync(path.join(dir, f), "utf8"))
            .join("");
        },
        { timeout: 10_000 },
      )
      .toContain("scale * 21");
  } finally {
    first.proc.kill("SIGKILL");
  }

  // A brand-new process, a new port, a new origin — and the cell is still there,
  // which localStorage could never have managed.
  const second = await startDebuggee("frontend/e2e/debuggee.py", xdg);
  try {
    await page.goto(second.url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(page.locator(".cell .cm-content").first()).toContainText("scale * 21");
  } finally {
    second.proc.kill("SIGKILL");
  }
});

test("a pinned tab follows the selected frame", async ({ page }) => {
  const xdg = xdgRoot();
  const { proc, url } = await startDebuggee("frontend/e2e/debuggee.py", xdg);
  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Pin a cell to the frame we are paused in: a tab appears and takes over.
    await typeInFirstCell(page, "data.mean()");
    await page.getByRole("button", { name: "Pin cell" }).first().click();
    const computeTab = page.getByRole("tab", { name: "compute()" });
    await expect(computeTab).toHaveAttribute("aria-selected", "true");

    // Selecting a frame with no cells of its own leaves the console alone.
    await page.locator(".stack button", { hasText: "main" }).first().click();
    await expect(page.locator(".vars")).toContainText("label", { timeout: 10_000 });
    await expect(computeTab).toHaveAttribute("aria-selected", "true");

    // Away to Scratch, then back to the pinned frame: its tab returns, with the
    // cell in it.
    await page.getByRole("tab", { name: "Scratch" }).click();
    await page.locator(".stack button", { hasText: "compute" }).first().click();
    await expect(computeTab).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });
    await expect(page.locator(".cell .cm-content").first()).toContainText("data.mean()");
  } finally {
    proc.kill("SIGKILL");
  }
});

test("the user's library supplies setup cells and cells to insert", async ({ page }) => {
  const xdg = xdgRoot();
  fs.mkdirSync(path.join(xdg.config, "judb"), { recursive: true });
  fs.writeFileSync(
    path.join(xdg.config, "judb", "cells.py"),
    '# %% imports tags=["setup"]\nimport math\n\n# %% a saved one\nmath.tau\n',
  );
  const { proc, url } = await startDebuggee("frontend/e2e/debuggee.py", xdg);
  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // The setup cell is materialised at the top and runs itself once.
    const setup = page.locator(".cell.setup").first();
    await expect(setup).toContainText("import math");
    await expect(setup.locator(".count")).toHaveText("[1]", { timeout: 15_000 });

    // The other one is offered in the library, not run, until inserted.
    await page.getByRole("button", { name: "Saved cells" }).click();
    await page.getByRole("button", { name: "Insert" }).last().click();
    await expect(page.locator(".cell .cm-content").last()).toContainText("math.tau");
  } finally {
    proc.kill("SIGKILL");
  }
});
