"""Shared test helpers.

Imported as ``from helpers import ...``: ``tests/`` has no ``__init__.py``, so
pytest's default (prepend) import mode puts this directory on ``sys.path``.

Two ways to reach a running debuggee show up across the suite:

* **in-process** — construct a ``Debugger``, ``start_server(open_browser=False)``,
  and drive it over the websocket (:func:`ws_url`, :func:`recv_type`);
* **out-of-process** — spawn a real debuggee and scrape its URL from the output,
  either from a pipe (:func:`read_judb_url`) or from a pty when the test needs a
  controlling terminal (:func:`read_pty_for_url`).
"""

import asyncio
import os
import re
import subprocess
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientSession, ClientWebSocketResponse

# What `Debugger.start_server` prints to stderr; the only way into a spawned
# debuggee, since the port and token are both random.
URL_LINE = re.compile(r"judb: debugger UI at (\S+)")


async def recv_type(ws: ClientWebSocketResponse, want: str) -> dict[str, Any]:
    """Receive messages until one of type ``want`` arrives."""
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if msg.get("type") == want:
            return msg


def ws_url(url: str) -> str:
    """The websocket endpoint for a tokenized UI URL."""
    q = urlparse(url)
    return f"ws://{q.hostname}:{q.port}/ws?token={parse_qs(q.query)['token'][0]}"


def has_error(outputs: list[dict[str, Any]], ename: str) -> bool:
    """True if a cell result carries an error output of type ``ename``."""
    return any(
        o["kind"] == "error" and o["data"].get("ename") == ename for o in outputs
    )


async def pause_then_continue(url: str) -> dict[str, Any]:
    """Connect, capture the first ``paused``, resume, and return that message."""
    async with ClientSession() as session, session.ws_connect(ws_url(url)) as ws:
        paused = await recv_type(ws, "paused")
        await ws.send_json({"cmd": "continue"})
        await recv_type(ws, "running")
        return paused


def read_judb_url(proc: subprocess.Popen[str], lines: list[str]) -> str:
    """Drain a subprocess' output on a thread and return judb's UI URL.

    Draining continuously keeps the pipe from filling, which would otherwise
    block the paused subprocess once it resumes and writes its report.
    """
    found = threading.Event()
    box: dict[str, str] = {}

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            match = URL_LINE.search(line)
            if match and "url" not in box:
                box["url"] = match.group(1)
                found.set()

    threading.Thread(target=reader, daemon=True).start()
    if not found.wait(timeout=30):
        raise AssertionError("never saw judb URL. output:\n" + "".join(lines))
    return box["url"]


def read_pty_for_url(master: int, sink: list[str], timeout: float = 30) -> str:
    """Drain a pty master on a thread and return judb's UI URL.

    As :func:`read_judb_url`, but for a debuggee given a controlling terminal;
    it also keeps collecting whatever the debuggee prints on its way out.
    """
    found = threading.Event()
    box: dict[str, str] = {}

    def reader() -> None:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # EIO once the child is gone
                return
            if not chunk:
                return
            sink.append(chunk.decode(errors="replace"))
            match = URL_LINE.search("".join(sink))
            if match and "url" not in box:
                box["url"] = match.group(1)
                found.set()

    threading.Thread(target=reader, daemon=True).start()
    if not found.wait(timeout=timeout):
        raise AssertionError("never saw judb URL. output:\n" + "".join(sink))
    return box["url"]


def wait_for_exit(pid: int, timeout: float) -> int | None:
    """waitpid with a deadline; None if the child is still running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return status
        time.sleep(0.05)
    return None
