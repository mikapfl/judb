import { defineConfig, devices } from "@playwright/test";

// e2e runs the *real* judb server (a Python debuggee) and drives the built
// bundle in a browser — the browser analog of tests/test_server.py.
// Prereq: `pnpm run build` (so judb/static/index.html is current) and
// `pnpm exec playwright install chromium`.
//
// Tests run in parallel: each spawns its *own* debuggee subprocess on an
// OS-picked random port (127.0.0.1:0) with no shared state, so there is nothing
// to serialize. Workers are capped rather than left at Playwright's default
// (half the cores): every worker runs a Chromium *and* a numpy/matplotlib
// debuggee, and one test asserts a SIGINT unwinds a sleep within a wall-clock
// budget — too much CPU contention would make that flaky. 2 on CI (modest
// runners), 4 locally.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  timeout: 30_000,
  use: { ...devices["Desktop Chrome"] },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
