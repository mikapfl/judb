import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..");

/** Launch a Python debuggee (defaults to the live-pause fixture); resolve once
 *  it prints its server URL. */
function startDebuggee(
  script = "frontend/e2e/debuggee.py",
): Promise<{ proc: ChildProcessWithoutNullStreams; url: string }> {
  const proc = spawn("uv", ["run", "python", script], {
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

    // Type a plot cell into the notebook cell (the editable CodeMirror) and run.
    const cell = page.locator(".cell .cm-content").first();
    await cell.click();
    await page.keyboard.type("import matplotlib.pyplot as plt; plt.plot(data)");
    await page.getByRole("button", { name: "Run cell" }).first().click();

    // The paused frame's `data` renders as an inline PNG.
    const img = page.locator(".cell img");
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
    const cell = page.locator(".cell .cm-content").first();
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

test("toggle a breakpoint from the source gutter", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Click a line in the breakpoint gutter; a red dot appears after the
    // set_break round-trip, and clicking the same line clears it.
    const gutterLine = page
      .locator(".source .cm-breakpoint-gutter .cm-gutterElement:visible")
      .first();
    const dots = page.locator(".source .cm-breakpoint");

    await expect(dots).toHaveCount(0);
    await gutterLine.click();
    await expect(dots).toHaveCount(1, { timeout: 10_000 });
    await gutterLine.click();
    await expect(dots).toHaveCount(0, { timeout: 10_000 });
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("set a conditional breakpoint from the gutter popover", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Right-click a gutter line to open the breakpoint editor, give it a
    // condition, and save. The marker becomes a diamond (cm-breakpoint-cond).
    const gutterLine = page
      .locator(".source .cm-breakpoint-gutter .cm-gutterElement:visible")
      .first();
    await gutterLine.click({ button: "right" });

    const popover = page.locator(".source .popover");
    await expect(popover).toBeVisible();
    await popover.getByPlaceholder("e.g. i == 3").fill("scale == 2.0");
    await popover.getByRole("button", { name: "Save" }).click();

    await expect(popover).toBeHidden();
    await expect(page.locator(".source .cm-breakpoint-cond")).toHaveCount(1, {
      timeout: 10_000,
    });
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("the breakpoints pane lists a breakpoint and removes it", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    const pane = page.locator(".breakpoints");
    await expect(pane).toContainText("No breakpoints");

    // Set a breakpoint from the gutter; the pane lists it (with its file).
    const gutterLine = page
      .locator(".source .cm-breakpoint-gutter .cm-gutterElement:visible")
      .first();
    await gutterLine.click();
    await expect(pane.locator("li")).toHaveCount(1, { timeout: 10_000 });
    await expect(pane).toContainText("debuggee.py");

    // Remove it from the pane; both the row and the gutter dot go.
    await pane.getByRole("button", { name: "Remove breakpoint" }).click();
    await expect(pane).toContainText("No breakpoints", { timeout: 10_000 });
    await expect(page.locator(".source .cm-breakpoint")).toHaveCount(0);
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("interrupt a runaway console cell", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Run a cell that never returns. Python auto-indent supplies the body indent.
    const cell = page.locator(".cell .cm-content").first();
    await cell.click();
    await page.keyboard.type("while True:");
    await page.keyboard.press("Enter");
    await page.keyboard.type("pass");
    await page.getByRole("button", { name: "Run cell" }).first().click();

    // The cell is now spinning: it shows as pending and Interrupt enables.
    const interrupt = page.getByRole("button", { name: "Interrupt" });
    await page.locator(".cell .pending").waitFor({ timeout: 10_000 });
    await expect(interrupt).toBeEnabled();
    // Let the debuggee thread actually enter the cell before interrupting it
    // (the pending marker is set optimistically, before the backend starts).
    await page.waitForTimeout(600);

    await interrupt.click();

    // The runaway cell unwinds as a KeyboardInterrupt and the console frees up.
    await expect(page.locator(".cells")).toContainText("KeyboardInterrupt", {
      timeout: 15_000,
    });
    await expect(interrupt).toBeDisabled();
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("interrupt a cell blocked in a C call, not just a Python loop", async ({ page }) => {
  // The user story: a console cell is taking forever and the Interrupt button
  // stops it *now*, even though execution is inside a blocking C call rather
  // than a Python loop the interpreter can break between bytecodes. That works
  // because judb sends a real SIGINT when the debuggee is the main thread, so
  // the syscall returns EINTR — exactly as Ctrl+C would.
  //
  // (Limit worth knowing: this covers blocking *syscalls*. A long CPU-bound C
  // routine such as a big BLAS call still only unwinds once it returns to
  // Python — a CPython-wide constraint, cf. Jupyter's "interrupt kernel".)
  // The break-a-blocking-C-call guarantee is POSIX-only: it relies on a real
  // SIGINT (pthread_kill), which the debugger uses only where it exists. On
  // Windows the interrupt falls back to SetAsyncExc, which lands at the next
  // bytecode and so cannot preempt a C-level `time.sleep` — by design (see
  // Debugger.interrupt). The pure-Python runaway-loop interrupt is still covered
  // on every OS by the test above.
  test.skip(process.platform === "win32", "SIGINT-breaks-a-C-call is POSIX-only");

  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    const cell = page.locator(".cell .cm-content").first();
    await cell.click();
    await page.keyboard.type("import time; time.sleep(5)");
    await page.getByRole("button", { name: "Run cell" }).first().click();

    const interrupt = page.getByRole("button", { name: "Interrupt" });
    await page.locator(".cell .pending").waitFor({ timeout: 10_000 });
    await expect(interrupt).toBeEnabled();
    // The pending marker is optimistic, set before the backend starts the cell;
    // interrupting ahead of that is a no-op, so let the debuggee get into it.
    await page.waitForTimeout(600);

    const startedAt = Date.now();
    await interrupt.click();
    await expect(page.locator(".cells")).toContainText("KeyboardInterrupt", {
      timeout: 15_000,
    });
    const elapsed = Date.now() - startedAt;

    // ~4.4s of the sleep was still to run. Breaking out well inside that is the
    // whole point: without a real SIGINT we would have waited the sleep out.
    expect(elapsed).toBeLessThan(2000);
    await expect(interrupt).toBeDisabled();
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("interactive matplotlib: render an in-frame plot, then zoom it", async ({ page }) => {
  const { proc, url } = await startDebuggee();

  try {
    // A matplotlib figure is ~640 px wide and the drag below aims at absolute
    // coordinates inside it, so give the console room next to an 80-column
    // source rather than letting the responsive rules decide (at the default
    // 1280×720 the console is narrower than the figure and the drag misses).
    await page.setViewportSize({ width: 1700, height: 1000 });
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Opt into the WebAgg-backed interactive backend, then plot in a new cell.
    await page.locator(".cell .cm-content").first().click();
    await page.keyboard.type("%matplotlib judb");
    await page.getByRole("button", { name: "Run cell" }).first().click();

    await page.locator(".add-cell").click();
    await page.keyboard.type("import matplotlib.pyplot as plt; plt.plot(data); None");
    await page.getByRole("button", { name: "Run cell" }).nth(1).click();

    // The interactive canvas mounts, sizes to the figure, and renders real
    // pixels (a blank canvas serialises to ~3.4 KB; a rendered one is far more).
    const canvas = page.locator(".webagg-host canvas").first();
    await canvas.waitFor({ timeout: 15_000 });
    await expect
      .poll(async () => canvas.evaluate((c: HTMLCanvasElement) => c.width), { timeout: 10_000 })
      .toBeGreaterThan(300);
    const before = await canvas.evaluate((c: HTMLCanvasElement) => c.toDataURL());
    expect(before.length).toBeGreaterThan(8000);

    // Regression guard: the rubberband canvas is stacked *over* the plot canvas,
    // so it must stay transparent — an opaque background there hides the figure
    // (it did, in Firefox, when the white backing wasn't scoped to .mpl-canvas).
    const rubberbandBg = await page
      .locator(".webagg-host canvas:not(.mpl-canvas)")
      .first()
      .evaluate((c: HTMLCanvasElement) => getComputedStyle(c).backgroundColor);
    expect(rubberbandBg).toBe("rgba(0, 0, 0, 0)");

    // The WebAgg navigation toolbar is present (icons served from /_images; the
    // zoom tool's icon carries a "Zoom to rectangle" alt/tooltip).
    const zoom = page.locator(".webagg-host img[alt*='Zoom to rectangle']");
    await expect(zoom).toBeVisible();

    // Select the zoom tool (wait for the mode to activate on the backend), then
    // drag a rubberband well inside the axes: the figure re-renders server-side
    // (in the paused frame) and the canvas content changes.
    await zoom.click();
    await page.waitForTimeout(400);
    const box = await canvas.boundingBox();
    if (!box) throw new Error("canvas has no bounding box");
    await page.mouse.move(box.x + 180, box.y + 150);
    await page.mouse.down();
    await page.mouse.move(box.x + 400, box.y + 320, { steps: 15 });
    await page.mouse.up();
    await expect
      .poll(async () => canvas.evaluate((c: HTMLCanvasElement) => c.toDataURL()), {
        timeout: 10_000,
      })
      .not.toBe(before);
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("post-mortem: the exception pane shows the crash after continue", async ({ page }) => {
  const { proc, url } = await startDebuggee("frontend/e2e/crash_debuggee.py");

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // An ordinary pause: the exception pane is empty.
    const pane = page.locator(".exception");
    await expect(pane).toContainText("No exception.");
    await expect(page.locator(".exc-tb")).toHaveCount(0);

    // Continue → the debuggee crashes and the debugger re-pauses post-mortem.
    await page.getByRole("button", { name: "Continue" }).click();

    // The exception pane now names the crash and shows its traceback.
    await expect(pane).toContainText("ValueError", { timeout: 15_000 });
    await expect(pane).toContainText("bad rows");
    await expect(page.locator(".exc-tb")).toContainText("ValueError: bad rows");
    await expect(page.locator(".exc-tb")).toContainText("compute");

    // The failing line is marked and its source is highlighted here in the
    // browser, from the same palette as the source editor — so the traceback
    // and the Source pane paint `raise` with the identical colour.
    // One marked line per frame; the innermost (last) one is where it raised.
    const failing = page.locator(".exc-tb .row.current").last();
    await expect(failing).toContainText('raise ValueError("bad rows")');
    const tbKeyword = failing.locator("span", { hasText: /^raise$/ }).first();
    const srcKeyword = page
      .locator(".cm-content span", { hasText: /^raise$/ })
      .first();
    const color = (l: typeof tbKeyword) =>
      l.evaluate((n) => getComputedStyle(n).color);
    expect(await color(tbKeyword)).toBe(await color(srcKeyword));

    // Frames still on the stack are selectable from the traceback itself, just
    // like the call-stack pane: click `main` and the variables retarget to it.
    // (This only works because the backend spells traceback filenames with
    // `Bdb.canonic`, exactly as the `stack` message does.)
    await expect(page.locator(".vars")).toContainText("rows");
    await page.locator(".exc-tb button.frame-loc", { hasText: "in main" }).click();
    await expect(page.locator(".vars")).toContainText("data");
    await expect(page.locator(".vars")).not.toContainText("rows");
    await expect(
      page.locator(".exc-tb button.frame-loc.selected"),
    ).toContainText("in main");
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

test("open a file the debuggee hasn't reached, break in it, and stop there", async ({
  page,
}) => {
  // The multi-file criterion: the Source pane can only ever show a file some
  // frame is in, which makes the breakpoint you actually want — in code you
  // have not run yet — impossible to place. `later.py` is imported only after
  // the pause, so it is unreachable through the stack.
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(page.locator(".source .filebar .name")).toHaveText("debuggee.py");

    // Open it by path (relative to the debuggee's working directory).
    await page.getByRole("button", { name: "Open file…" }).click();
    await page.getByLabel("Open file").fill("frontend/e2e/later.py");
    await page.getByLabel("Open file").press("Enter");

    // The pane says plainly that this is not where the debuggee is stopped.
    await expect(page.locator(".source .filebar .name")).toHaveText("later.py");
    await expect(page.locator(".source .filebar")).toContainText("not the paused frame");
    await expect(page.locator(".source")).toContainText("LATER_FRAME");

    // Break on `summary = ...` (line 11) — the gutter now targets *this* file.
    await page
      .locator(".source .cm-breakpoint-gutter .cm-gutterElement:visible")
      .nth(10)
      .click();
    await expect(page.locator(".breakpoints")).toContainText("later.py");
    await expect(page.locator(".breakpoints li")).toContainText("line 11");

    // Back to the frame, then navigate to that breakpoint from the pane — a
    // file no frame is in is reachable from there too.
    await page.getByRole("button", { name: "Back to frame" }).click();
    await expect(page.locator(".source .filebar .name")).toHaveText("debuggee.py");
    await page.locator(".breakpoints").getByRole("button", { name: "line 11" }).click();
    await expect(page.locator(".source .filebar .name")).toHaveText("later.py");
    await expect(page.locator(".source .cm-marked-line")).toHaveCount(1);

    // Continue: the module is imported, called, and we stop inside it — with
    // the pane back to showing the frame the debuggee is really in.
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.locator("header .loc")).toContainText("finish()", {
      timeout: 15_000,
    });
    await expect(page.locator("header .loc")).toContainText("later.py:11");
    await expect(page.locator(".source .filebar")).not.toContainText(
      "not the paused frame",
    );
    await expect(page.locator(".vars")).toContainText("value");
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("watch expressions follow the selected frame and survive a reload", async ({
  page,
}) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Two watches: one that resolves in the paused frame (`compute`), one that
    // only resolves in its caller.
    const add = page.getByLabel("Add watch expression");
    await add.fill("scale * 10");
    await add.press("Enter");
    await add.fill("label");
    await add.press("Enter");

    const rows = page.locator(".watch li.row");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator(".summary")).toContainText("20.0", {
      timeout: 10_000,
    });
    // Out of scope here — a per-row error, with the other watch unaffected.
    await expect(rows.nth(1).locator(".err")).toContainText("NameError");

    // Selecting `main` re-evaluates both there, without being asked.
    await page.locator(".stack button", { hasText: "main" }).click();
    await expect(rows.nth(1).locator(".summary")).toContainText("MAIN_FRAME");
    await expect(rows.nth(0).locator(".err")).toContainText("NameError");

    // The list is the browser's, persisted: a reload brings it back and the
    // backend (which was told nothing new) re-evaluates it on reconnect —
    // against `main`, since the frame selection survives the reload too.
    await page.reload();
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(page.locator(".vars")).toContainText("label");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(1).locator(".summary")).toContainText("MAIN_FRAME", {
      timeout: 10_000,
    });

    // Removing a row drops it from the pane.
    await rows.nth(1).getByRole("button", { name: "Remove watch" }).click();
    await expect(rows).toHaveCount(1);
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("refreshing the page while paused restores the UI", async ({ page }) => {
  // The everyday case: the user hits F5 (or the tab is restored) while the
  // debuggee sits paused. Everything the first connection consumed is gone from
  // the server's outbound buffer, so without a state replay the reloaded tab
  // would come up blank on a debuggee that is still very much paused.
  const { proc, url } = await startDebuggee();

  try {
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(page.locator(".source")).toContainText("np.linspace");

    // Set a breakpoint before reloading: it must survive the refresh, in both
    // the gutter and the breakpoints pane (regression — a reload used to drop
    // every breakpoint, since the set arrives as a one-shot message).
    await page
      .locator(".source .cm-breakpoint-gutter .cm-gutterElement:visible")
      .first()
      .click();
    await expect(page.locator(".source .cm-breakpoint")).toHaveCount(1, { timeout: 10_000 });
    await expect(page.locator(".breakpoints li")).toHaveCount(1);

    await page.reload();

    // Same paused frame, repopulated from the replay.
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(page.locator(".source")).toContainText("np.linspace");
    await expect(page.locator(".vars")).toContainText("data");
    await expect(page.locator(".stack")).toContainText("compute");

    // The breakpoint came back with the replayed state.
    await expect(page.locator(".source .cm-breakpoint")).toHaveCount(1);
    await expect(page.locator(".breakpoints li")).toHaveCount(1);

    // And it is still driveable: the console runs in the paused frame.
    const cell = page.locator(".cell .cm-content").first();
    await cell.click();
    await page.keyboard.type("len(data)");
    await page.getByRole("button", { name: "Run cell" }).first().click();
    await expect(page.locator(".cells")).toContainText("50", { timeout: 15_000 });
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

// --- responsive layout ---------------------------------------------------

/** The pane box with this header title (the header is the only place it appears). */
const paneBox = (page: Page, title: string) =>
  page.locator(`.panebox:has(h2 .title:text-is("${title}"))`);

/** How many columns of code the source editor currently fits, measured in the
 *  editor's own font — the number the 80-column floor is about. */
async function sourceColumns(page: Page): Promise<number> {
  return page.evaluate(() => {
    const content = document.querySelector(".source .cm-content");
    if (!content) return 0;
    const style = getComputedStyle(content);
    const ctx = document.createElement("canvas").getContext("2d")!;
    ctx.font = `${style.fontSize} ${style.fontFamily}`;
    const charPx = ctx.measureText("0".repeat(80)).width / 80;
    return Math.floor(content.getBoundingClientRect().width / charPx);
  });
}

/** Distinct rows the secondary panes are laid out in, top to bottom. */
async function secondaryRowCount(page: Page): Promise<number> {
  const boxes = await page.locator(".panebox").evaluateAll((els) =>
    els
      .filter((el) => !el.querySelector(".source, .notebook"))
      .map((el) => Math.round(el.getBoundingClientRect().y)),
  );
  return new Set(boxes).size;
}

test("the source keeps 80 columns — the console folds under it when it can't", async ({
  page,
}) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Roomy: console beside the source (same top, further right), and the
    // source still has well over PEP 8's 80 columns.
    const wideSource = (await paneBox(page, "Source").boundingBox())!;
    const wideConsole = (await paneBox(page, "Notebook console — runs in the paused frame")
      .boundingBox())!;
    expect(wideConsole.x).toBeGreaterThan(wideSource.x + wideSource.width - 1);
    expect(Math.abs(wideConsole.y - wideSource.y)).toBeLessThan(2);
    expect(await sourceColumns(page)).toBeGreaterThanOrEqual(80);

    // Squeezed but still wide enough for both: the split shifts in the source's
    // favour rather than letting it drop below the floor.
    await page.setViewportSize({ width: 1100, height: 900 });
    await expect(async () => {
      expect(await sourceColumns(page)).toBeGreaterThanOrEqual(80);
    }).toPass({ timeout: 5_000 });
    const midConsole = (await paneBox(page, "Notebook console — runs in the paused frame")
      .boundingBox())!;
    expect(midConsole.x).toBeGreaterThan(0); // still side by side

    // Too narrow for both: the console folds *under* the source, which then
    // gets the whole width back.
    await page.setViewportSize({ width: 780, height: 900 });
    await expect(async () => {
      const source = (await paneBox(page, "Source").boundingBox())!;
      const console_ = (await paneBox(
        page,
        "Notebook console — runs in the paused frame",
      ).boundingBox())!;
      expect(console_.y).toBeGreaterThan(source.y + source.height - 1);
      expect(console_.x).toBeLessThan(2);
      expect(source.width).toBeGreaterThan(700);
    }).toPass({ timeout: 5_000 });
    expect(await sourceColumns(page)).toBeGreaterThanOrEqual(80);
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

/** Drag the source/console splitter to an absolute x. */
async function dragSplitterTo(page: Page, x: number): Promise<void> {
  const splitter = page
    .locator("main > .splitpanes > .splitpanes__pane")
    .first()
    .locator("> .splitpanes > .splitpanes__splitter");
  const grip = (await splitter.boundingBox())!;
  const y = grip.y + grip.height / 2;
  await page.mouse.move(grip.x + grip.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 10 });
  await page.mouse.up();
}

test("a source narrower than 80 columns is the user's call, and it sticks", async ({
  page,
}) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.setViewportSize({ width: 1500, height: 900 });
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    // Haul the splitter left, well past the floor: the user wants a sliver of
    // source and a wide console, and gets it.
    await dragSplitterTo(page, 20);
    const slim = await sourceColumns(page);
    expect(slim).toBeLessThan(40);

    // The window moving under them does not overrule that — neither wider…
    await page.setViewportSize({ width: 1700, height: 900 });
    await page.waitForTimeout(300);
    expect(await sourceColumns(page)).toBeLessThan(60);
    // …nor narrower.
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.waitForTimeout(300);
    expect(await sourceColumns(page)).toBeLessThan(40);

    // Dragging back to a comfortable width re-arms the automatic floor: from
    // here on the window is once again held to 80 columns.
    await page.setViewportSize({ width: 1500, height: 900 });
    await page.waitForTimeout(300);
    await dragSplitterTo(page, 825);
    expect(await sourceColumns(page)).toBeGreaterThanOrEqual(80);

    await page.setViewportSize({ width: 1100, height: 900 });
    await expect(async () => {
      expect(await sourceColumns(page)).toBeGreaterThanOrEqual(80);
    }).toPass({ timeout: 5_000 });
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("the secondary panes reflow into two rows, then three, as the window narrows", async ({
  page,
}) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });

    await expect(async () => expect(await secondaryRowCount(page)).toBe(1)).toPass({
      timeout: 5_000,
    });

    await page.setViewportSize({ width: 1000, height: 900 });
    await expect(async () => expect(await secondaryRowCount(page)).toBe(2)).toPass({
      timeout: 5_000,
    });

    await page.setViewportSize({ width: 620, height: 900 });
    await expect(async () => expect(await secondaryRowCount(page)).toBe(3)).toPass({
      timeout: 5_000,
    });

    // Nothing has been pushed off the side: a horizontally scrolling body would
    // slide the whole grid out from under the pointer.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});

test("a closed pane stays closed across a reload and comes back from the menu", async ({
  page,
}) => {
  const { proc, url } = await startDebuggee();

  try {
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto(url);
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(paneBox(page, "Exception")).toBeVisible();

    // Close it from its own header…
    await paneBox(page, "Exception").locator("h2 .close").click();
    await expect(paneBox(page, "Exception")).toHaveCount(0);
    // …and the toolbar says one pane is hidden on purpose.
    await expect(page.locator(".panemenu .count")).toHaveText("1 hidden");

    // It is a preference, so it survives a reload (localStorage, like the theme).
    await page.reload();
    await expect(page.locator(".status")).toHaveText("paused", { timeout: 15_000 });
    await expect(paneBox(page, "Exception")).toHaveCount(0);

    // The menu is the way back.
    await page.getByRole("button", { name: "Panes" }).click();
    await page.getByRole("menu").getByText("Show all").click();
    await expect(paneBox(page, "Exception")).toBeVisible();
    await expect(page.locator(".panemenu .count")).toHaveCount(0);
  } finally {
    if (proc.exitCode === null) proc.kill("SIGKILL");
  }
});
