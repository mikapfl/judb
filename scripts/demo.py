"""A terminal driver for the Phase 0 debugger — the browser frontend's stand-in.

The debuggee runs on a background thread; when it pauses, its interaction loop
blocks on the inbound queue while *this* (main) thread reads what you type and
feeds it in. Type Python to run a cell **in the paused frame** (try ``data`` or
``plt.plot(data)``); rich outputs render in the terminal and PNGs open in your
image viewer. Blank line / ``c`` continues, ``n`` next, ``s`` step, ``q`` quits.

    uv run python scripts/demo.py
"""

import base64
import contextlib
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

from judb import Debugger


def debuggee(dbg: Debugger) -> None:
    """A tiny 'scientific' routine to pause inside."""
    try:
        data = np.linspace(0.0, 10.0, 50)
        dbg.set_trace()  # pauses at the next line
        total = 0.0
        for i in range(len(data)):
            total += float(data[i])  # step through this loop with `n`
        print(f"[debuggee] finished, total = {total}")
    finally:
        dbg.outbound.put({"type": "finished"})


def _open(path: Path) -> None:
    # Best-effort: xdg-open may be absent (headless), which is fine.
    with contextlib.suppress(Exception):
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def render(msg: dict[str, Any]) -> None:
    for out in msg["outputs"]:
        kind, data = out["kind"], out["data"]
        if kind == "stream":
            print(data["text"], end="")
        elif "image/png" in data:
            raw = base64.b64decode(data["image/png"])
            path = Path(tempfile.mkdtemp(prefix="judb_")) / "cell.png"
            path.write_bytes(raw)
            print(f"  🖼  PNG ({len(raw)} bytes) -> {path}  (opening…)")
            _open(path)
        elif "text/html" in data:
            path = Path(tempfile.mkdtemp(prefix="judb_")) / "cell.html"
            path.write_text(data["text/html"])
            print(f"  🌐 HTML -> {path}")
            print("     text/plain:\n" + data.get("text/plain", ""))
        elif "text/plain" in data:
            print(data["text/plain"])
        elif kind == "error":
            print(f"  ⚠ {data['ename']}: {data['evalue']}")


COMMANDS = {"c": "continue", "": "continue", "n": "next", "s": "step", "q": "quit"}


def main() -> None:
    import matplotlib.pyplot as plt

    dbg = Debugger()
    # Convenience: seed `plt` so the headline `plt.plot(data)` works immediately.
    # (Persists across cells; the paused frame's own vars are layered on top.)
    dbg.console.shell.user_ns["plt"] = plt

    print(
        "judb Phase 0 demo — the debuggee runs in a thread and pauses below.\n"
        "At each pause, type Python to run it IN the paused frame. Try:\n"
        "    data            plt.plot(data)            data.mean()\n"
        "Commands:  <enter>/c = continue   n = next   s = step   q = quit\n"
    )

    thread = threading.Thread(target=debuggee, args=(dbg,))
    thread.start()

    while True:
        msg = dbg.outbound.get()
        if msg["type"] == "finished":
            break
        if msg["type"] != "paused":
            continue

        print(
            f"\n⏸  paused in {msg['function']}() at "
            f"{Path(msg['filename']).name}:{msg['lineno']}"
            f"   locals: {', '.join(msg['locals']) or '(none)'}"
        )
        while True:  # inner REPL for this pause
            try:
                line = input(f"judb:{msg['lineno']}> ")
            except EOFError:
                dbg.inbound.put({"cmd": "quit"})
                print()
                return
            s = line.strip()
            if s in COMMANDS:
                cmd = COMMANDS[s]
                dbg.inbound.put({"cmd": cmd})
                if cmd == "quit":
                    return
                break  # step/continue: wait for the next pause (or finish)
            dbg.inbound.put({"cmd": "execute_cell", "code": line})
            render(dbg.outbound.get())

    thread.join(timeout=5)
    print("[demo] done.")


if __name__ == "__main__":
    main()
