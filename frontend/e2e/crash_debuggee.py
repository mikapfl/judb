"""A debuggee for the exception-pane e2e: pause once (so the browser connects),
then crash on `continue` so the debugger enters post-mortem and the Exception
pane populates. Mirrors what `python -m judb` does when a script raises past its
own code (catch the crash -> `post_mortem`)."""

from judb import Debugger

dbg = Debugger()
url = dbg.start_server(open_browser=False)
print(url, flush=True)  # the Playwright test reads this line


def compute(rows: list[int]) -> int:
    total = sum(rows)  # noqa: F841 — inspectable from the post-mortem frame
    raise ValueError("bad rows")


def main() -> None:
    data = [1, 2, 3]
    dbg.set_trace()  # first pause; the browser connects here
    compute(data)  # after `continue`, this raises


try:
    main()
except BaseException as exc:  # noqa: BLE001 — hand the crash to the debugger
    dbg.post_mortem(exc)
print("DEBUGGEE_DONE", flush=True)
