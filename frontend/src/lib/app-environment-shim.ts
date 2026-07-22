// svelte-splitpanes imports SvelteKit's `$app/environment` for `browser`
// detection. We don't use SvelteKit (PHASE2_STACK.md §1), and judb only ever
// runs in a real browser, so this shim is aliased in via vite.config.ts.
export const browser = true;
export const dev = false;
export const building = false;
export const version = "";
