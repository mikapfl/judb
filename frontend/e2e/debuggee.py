"""A tiny debuggee for the Playwright e2e: start the judb server, print its URL,
then pause on the main thread so the process stays alive (and serving the built
frontend) until the browser sends `continue`. Mirrors tests/test_phase1.py, but
driven by a real browser instead of a raw websocket."""

import numpy as np

from judb import Debugger

dbg = Debugger()
url = dbg.start_server(open_browser=False)
print(url, flush=True)  # the Playwright test reads this line

data = np.linspace(0.0, 10.0, 50)
dbg.set_trace()  # blocks the main thread here until the browser continues
print("DEBUGGEE_DONE", flush=True)
