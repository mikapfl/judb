// Loads matplotlib's own WebAgg client (`mpl.js`) on demand, served by the judb
// backend at `/_mpl.js` so it always matches the user's installed matplotlib
// version (see judb/server.py, judb/mpl_backend.py). It defines a global `mpl`
// with the `mpl.figure` constructor the interactive-canvas component uses.

/** The subset of matplotlib's `mpl.figure` we drive. It builds its own DOM
 *  (header + canvas + toolbar) inside `parentElement` and talks over a
 *  websocket-like `socket`. */
export interface MplFigure {
  canvas: HTMLCanvasElement;
  buttons: Record<string, HTMLButtonElement>;
  ws: MplSocket;
}

/** The websocket-like shim `mpl.figure` expects. `binaryType` being defined is
 *  what makes it advertise binary (diff-image) support to the backend. */
export interface MplSocket {
  binaryType: string;
  // WebSocket.OPEN (1). mpl.js's ResizeObserver bails unless this reads 1, so the
  // canvas would never size to the figure without it.
  readyState: number;
  onopen: () => void;
  onmessage: (evt: { data: unknown }) => void;
  send: (payload: string) => void;
  close: () => void;
}

export interface MplGlobal {
  figure: new (
    id: string,
    socket: MplSocket,
    ondownload: (fig: MplFigure, format: string | null) => void,
    parentElement: HTMLElement,
  ) => MplFigure;
}

declare global {
  interface Window {
    mpl?: MplGlobal;
  }
}

let loading: Promise<MplGlobal> | null = null;

export function loadMplClient(): Promise<MplGlobal> {
  if (window.mpl?.figure) return Promise.resolve(window.mpl);
  if (loading) return loading;
  const token = new URLSearchParams(location.search).get("token") ?? "";
  loading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `/_mpl.js?token=${encodeURIComponent(token)}`;
    script.onload = () => {
      if (window.mpl?.figure) resolve(window.mpl);
      else reject(new Error("mpl.js loaded but did not initialise `mpl.figure`"));
    };
    script.onerror = () => reject(new Error("failed to load mpl.js from the judb server"));
    document.head.appendChild(script);
  });
  return loading;
}
