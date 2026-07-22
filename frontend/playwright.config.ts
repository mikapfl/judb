import { defineConfig, devices } from "@playwright/test";

// e2e runs the *real* judb server (a Python debuggee) and drives the built
// bundle in a browser — the browser analog of tests/test_phase1.py.
// Prereq: `pnpm run build` (so judb/static/index.html is current) and
// `pnpm exec playwright install chromium`.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: { ...devices["Desktop Chrome"] },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
