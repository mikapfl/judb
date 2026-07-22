"""Wire-format data types shared between the debugger, console, and (later) server.

Outputs use the Jupyter mime-bundle convention on purpose: a dict keyed by mime
type (e.g. ``text/plain``, ``text/html``, ``image/png``). Keeping this identical
to Jupyter means the eventual frontend can render bundles with standard tooling
(``@jupyterlab/rendermime``) with zero backend change.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Output:
    """A single rich output, mirroring a Jupyter output message.

    ``kind`` is one of ``"execute_result"`` (value of the last expression),
    ``"display_data"`` (an explicit ``display(...)`` or a flushed matplotlib
    figure), ``"stream"`` (stdout/stderr text), or ``"error"`` (a traceback).
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def stream(cls, name: str, text: str) -> "Output":
        # name is "stdout" or "stderr"; shape matches a Jupyter stream message.
        return cls(kind="stream", data={"name": name, "text": text})

    @classmethod
    def error(cls, ename: str, evalue: str, traceback: list[str]) -> "Output":
        return cls(
            kind="error",
            data={"ename": ename, "evalue": evalue, "traceback": traceback},
        )

    def mime_types(self) -> list[str]:
        """The mime types carried by a result/display output (for introspection)."""
        if self.kind in ("execute_result", "display_data"):
            return sorted(self.data)
        return []


@dataclass
class CellResult:
    """The full result of executing one console cell."""

    outputs: list[Output] = field(default_factory=list)
    success: bool = True

    def first_of(self, mime: str) -> Any | None:  # noqa: ANN401
        """Return the first output's payload for ``mime``, or ``None``."""
        for out in self.outputs:
            if mime in out.data:
                return out.data[mime]
        return None
