"""The interactive-matplotlib (WebAgg) backend: `%matplotlib_interactive` swaps
matplotlib to judb's backend, a plotted figure becomes a mount-notice cell output
(not an inline PNG), and driving the client handshake produces a rendered frame.

Switching the backend is process-global, so each test restores it (and clears the
backend's figure registry) to keep the inline-PNG tests unaffected.
"""

from collections.abc import Iterator

import matplotlib
import pytest

from judb import Console, mpl_backend

# Outbound messages captured from the interactive backend's emitter.
Sent = list[dict[str, object]]


@pytest.fixture
def interactive_backend() -> Iterator[Sent]:
    """Restore the previous backend + registry after the test."""
    import matplotlib.pyplot as plt

    previous = matplotlib.get_backend()
    plt.close("all")
    sent: Sent = []
    mpl_backend.set_emitter(sent.append)
    try:
        yield sent
    finally:
        mpl_backend.set_emitter(None)
        plt.close("all")
        matplotlib.use(previous, force=True)
        mpl_backend.reset()


def test_matplotlib_judb_switches_backend(interactive_backend: Sent) -> None:
    console = Console()
    result = console.run_cell("%matplotlib judb")
    assert result.success
    assert mpl_backend.is_active()


def test_plot_becomes_a_webagg_mount_and_renders(interactive_backend: Sent) -> None:
    sent = interactive_backend
    console = Console()
    console.run_cell("%matplotlib judb")

    result = console.run_cell(
        "import matplotlib.pyplot as plt; plt.plot([3, 1, 4, 1, 5]); None"
    )
    mounts = [o for o in result.outputs if mpl_backend.WEBAGG_MIME in o.data]
    assert len(mounts) == 1
    fig_id = mounts[0].data[mpl_backend.WEBAGG_MIME]["id"]
    assert isinstance(fig_id, str)
    # No inline PNG in interactive mode — the figure is the live canvas instead.
    assert not any("image/png" in o.data for o in result.outputs)

    # Drive the client handshake + a draw request; the canvas answers with a
    # rendered PNG frame (delivered to the browser as a base64 `blob`).
    sent.clear()
    mpl_backend.dispatch(fig_id, {"type": "refresh"})
    mpl_backend.dispatch(fig_id, {"type": "draw", "figure_id": fig_id})
    frames = [m for m in sent if "blob" in m]
    assert frames, f"no rendered frame emitted; got {[m.get('json', m) for m in sent]}"


def test_download_renders_requested_vector_format(interactive_backend: Sent) -> None:
    """The toolbar's format selector saves via savefig, not the raster canvas —
    so svg/pdf really come out as svg/pdf, not a renamed PNG."""
    import base64

    sent = interactive_backend
    console = Console()
    console.run_cell("%matplotlib judb")
    result = console.run_cell(
        "import matplotlib.pyplot as plt; plt.plot([1, 2, 3]); None"
    )
    fig_id = next(
        o.data[mpl_backend.WEBAGG_MIME]["id"]
        for o in result.outputs
        if mpl_backend.WEBAGG_MIME in o.data
    )

    heads = {"svg": b"<?xml", "pdf": b"%PDF", "png": b"\x89PNG"}
    for fmt, head in heads.items():
        sent.clear()
        mpl_backend.download(fig_id, fmt)
        replies: list[dict[str, str]] = [m["download"] for m in sent if "download" in m]  # ty: ignore
        assert len(replies) == 1 and replies[0]["format"] == fmt
        assert base64.b64decode(replies[0]["data"]).startswith(head)


def test_inline_still_produces_png_after_switching_back(
    interactive_backend: Sent,
) -> None:
    """Switching to interactive and back to inline leaves inline PNGs working."""
    console = Console()
    console.run_cell("%matplotlib judb")
    console.run_cell("%matplotlib inline")
    result = console.run_cell(
        "import matplotlib.pyplot as plt; plt.plot([1, 2, 3]); None"
    )
    assert result.first_of("image/png") is not None
