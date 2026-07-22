import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { viteSingleFile } from "vite-plugin-singlefile";

// Builds the whole SPA into a single inlined `index.html` (JS + CSS inlined),
// emitted into `judb/static/` so the aiohttp server serves it with its existing
// one-line FileResponse. See PHASE2_STACK.md §2.
export default defineConfig(({ mode }) => ({
  plugins: [svelte(), viteSingleFile()],
  resolve: {
    alias: {
      // svelte-splitpanes reaches for SvelteKit's $app/environment; shim it.
      "$app/environment": fileURLToPath(
        new URL("./src/lib/app-environment-shim.ts", import.meta.url),
      ),
    },
    // Under Vitest only, force Svelte's *client* build (else it loads the SSR
    // build and mount() throws lifecycle_function_unavailable). Must NOT be set
    // for the production build: an empty/custom list clobbers Vite's default
    // conditions (which include `browser`) and reintroduces the same SSR bug.
    ...(mode === "test" ? { conditions: ["browser"] } : {}),
  },
  build: {
    outDir: "../judb/static",
    emptyOutDir: true,
    target: "esnext",
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts"],
  },
}));
