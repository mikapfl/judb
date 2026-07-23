import { mount } from "svelte";
import App from "./App.svelte";
import "./lib/tokens.css";
// Imported before mount so `<html data-theme>` is set with no flash of wrong theme.
import "./lib/theme.svelte";

const app = mount(App, { target: document.getElementById("app")! });

export default app;
