"""A tiny debuggee for the Playwright e2e: start the judb server, print its URL,
then pause on the main thread so the process stays alive (and serving the built
frontend) until the browser sends `continue`. Nested calls (main -> compute) give
the call-stack pane something to select. Driven by a real browser, not a raw
websocket (cf. tests/test_phase1.py, tests/test_phase2.py)."""

import numpy as np

from judb import Debugger

dbg = Debugger()
url = dbg.start_server(open_browser=False)
print(url, flush=True)  # the Playwright test reads this line


def compute(data: np.ndarray) -> float:
    scale = 2.0
    config = {"scale": scale, "tags": ["alpha", "beta"]}  # expandable in the vars pane
    dbg.set_trace()  # pauses here; innermost frame is `compute`
    return float(data.sum()) * scale * len(config)


def main() -> None:
    data = np.linspace(0.0, 10.0, 50)  # plotted from the browser
    label = "MAIN_FRAME"  # noqa: F841 — only visible once `main` is selected
    compute(data)


main()
print("DEBUGGEE_DONE", flush=True)
