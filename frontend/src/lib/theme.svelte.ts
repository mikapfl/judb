// Light/dark theming. One source of truth: `theme.mode` is the user's choice
// ("auto" follows the OS via `prefers-color-scheme`, else forced), and
// `theme.resolved` is the concrete "light" | "dark" we actually paint. The
// resolved value is stamped onto `<html data-theme>`, which selects the token
// set in tokens.css; every pane and both CodeMirror editors read those CSS
// custom properties, so a switch is a pure variable swap with no re-render.
//
// Applied eagerly at module load (before the app mounts) so there's no
// flash of the wrong theme — main.ts imports this first.

export type ThemeMode = "auto" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "judb-theme";
const MODES: ThemeMode[] = ["auto", "light", "dark"];

const media =
  typeof matchMedia === "function"
    ? matchMedia("(prefers-color-scheme: dark)")
    : null;

function loadMode(): ThemeMode {
  const v = (() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null; // localStorage can throw (private mode / sandboxed iframe).
    }
  })();
  return v === "light" || v === "dark" || v === "auto" ? v : "auto";
}

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode === "auto") return media?.matches ? "dark" : "light";
  return mode;
}

function apply(resolved: ResolvedTheme) {
  document.documentElement.dataset.theme = resolved;
}

class Theme {
  mode = $state<ThemeMode>(loadMode());
  resolved = $state<ResolvedTheme>(resolve(loadMode()));

  constructor() {
    apply(this.resolved);
    // Follow the OS while (and only while) the user hasn't forced a choice.
    media?.addEventListener("change", () => {
      if (this.mode === "auto") this.#set(resolve("auto"));
    });
  }

  #set(resolved: ResolvedTheme) {
    this.resolved = resolved;
    apply(resolved);
  }

  /** Set the user's choice, persist it, and repaint. */
  setMode(mode: ThemeMode) {
    this.mode = mode;
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // best-effort persistence
    }
    this.#set(resolve(mode));
  }

  /** Cycle auto -> light -> dark -> auto (the toolbar toggle). */
  cycle() {
    this.setMode(MODES[(MODES.indexOf(this.mode) + 1) % MODES.length]);
  }
}

export const theme = new Theme();
