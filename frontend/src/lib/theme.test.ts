import { beforeEach, describe, expect, it } from "vitest";
import { theme, type ThemeMode } from "./theme.svelte";

// jsdom has no matchMedia, so `auto` resolves to "light" (the safe fallback).
describe("theme store", () => {
  beforeEach(() => theme.setMode("auto"));

  it("defaults auto -> light and stamps <html data-theme>", () => {
    expect(theme.mode).toBe("auto");
    expect(theme.resolved).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("forces and persists an explicit choice", () => {
    theme.setMode("dark");
    expect(theme.resolved).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("judb-theme")).toBe("dark");
  });

  it("cycles auto -> light -> dark -> auto", () => {
    const seen: ThemeMode[] = [];
    for (let i = 0; i < 3; i++) {
      theme.cycle();
      seen.push(theme.mode);
    }
    expect(seen).toEqual(["light", "dark", "auto"]);
  });
});
