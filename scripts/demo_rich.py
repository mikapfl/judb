"""Rich-object demo: shows off judb's notebook-grade rendering.

    uv sync --extra example        # one-off: pulls in xarray/plotly/bokeh/…
    uv run python scripts/demo_rich.py

A browser tab opens paused deep in a call stack (main → simulate → build_scene),
with a spread of live datascience objects in the innermost frame. Type any of
these names in the Console pane (it runs *in the paused frame*) to see it render:

    ds            # xarray Dataset          -> collapsible HTML repr
    pl_df         # polars DataFrame        -> HTML table
    chart         # altair / Vega-Lite      -> interactive chart (CDN Vega)
    plotly_fig    # plotly Figure           -> interactive chart (Plotly JSON mime)
    bokeh_plot    # bokeh figure            -> interactive chart (CDN BokehJS)
    mpl_hover     # matplotlib + mpld3      -> static plot with hover tooltips

You can also just click each in the Variables pane. The heavyweight viz libs
live in the optional ``example`` extra, so a normal ``pip install judb`` stays
lean — see pyproject's ``[project.optional-dependencies]``.
"""

from __future__ import annotations

# Keep the debuggee's own figure-building off any GUI backend (judb's console
# switches to the inline backend itself once a cell runs).
import matplotlib

matplotlib.use("Agg")

import numpy as np
from IPython.display import HTML

import judb


def _bokeh_plot() -> HTML:
    """A real, interactive BokehJS plot as an HTML fragment (loads BokehJS from
    its CDN inside judb's sandboxed output iframe)."""
    from bokeh.embed import components
    from bokeh.plotting import figure
    from bokeh.resources import CDN

    x = np.linspace(0, 4 * np.pi, 200)
    fig = figure(
        width=520,
        height=320,
        title="Damped oscillation (hover / pan / zoom)",
        tools="hover,pan,box_zoom,wheel_zoom,reset",
        tooltips=[("x", "@x{0.00}"), ("y", "@y{0.000}")],
    )
    fig.line(x, np.exp(-0.2 * x) * np.sin(x), line_width=2, legend_label="signal")
    fig.scatter(
        x[::10], (np.exp(-0.2 * x) * np.sin(x))[::10], size=6, color="firebrick"
    )
    script, div = components(fig)
    libs = "".join(f'<script src="{u}"></script>' for u in CDN.js_files)
    return HTML(libs + div + script)


def _mpld3_hover() -> HTML:
    """A matplotlib scatter with per-point hover tooltips, via mpld3 (renders to
    interactive d3 HTML — hovering a point reveals its label)."""
    import matplotlib.pyplot as plt
    import mpld3
    from mpld3 import plugins

    rng = np.random.default_rng(1)
    n = 40
    x, y = rng.normal(size=n), rng.normal(size=n)
    fig, ax = plt.subplots(figsize=(6, 4), facecolor="white")
    ax.set_title("mpld3 scatter — hover a point for its label", size=12)
    scatter = ax.scatter(x, y, s=120 * rng.random(n) + 20, c=rng.random(n), alpha=0.6)
    labels = [f"point {i} ({xi:.2f}, {yi:.2f})" for i, (xi, yi) in enumerate(zip(x, y))]
    plugins.connect(fig, plugins.PointLabelTooltip(scatter, labels=labels))
    html = mpld3.fig_to_html(fig)
    plt.close(fig)
    # mpld3 renders the figure background transparent, so in dark mode the title
    # and tick labels (matplotlib black) would sit on the dark iframe. Wrap it in
    # a white card — the same always-white treatment judb gives matplotlib PNGs.
    return HTML(
        f'<div style="background:white;display:inline-block;padding:6px 10px;'
        f'border-radius:4px">{html}</div>'
    )


def build_scene(temps: np.ndarray) -> dict[str, float]:
    """Innermost frame: all the interesting objects are locals here."""
    import altair as alt
    import pandas as pd
    import plotly.express as px
    import plotly.io as pio
    import polars as pl
    import xarray as xr

    # plotly only emits its rich mime bundle under the mimetype renderer.
    pio.renderers.default = "plotly_mimetype"

    # --- xarray: a small gridded temperature field over (x, y, time) ----------
    ds = xr.Dataset(
        {"temperature": (("time", "y", "x"), temps)},
        coords={
            "time": np.arange(temps.shape[0]),
            "y": np.linspace(-2, 2, temps.shape[1]),
            "x": np.linspace(-2, 2, temps.shape[2]),
        },
        attrs={"description": "toy diffusion field", "units": "K"},
    )

    # --- polars: a tidy summary table ----------------------------------------
    means = temps.mean(axis=(1, 2))
    summary = {
        "time": np.arange(temps.shape[0]),
        "mean_temp": means,
        "max_temp": temps.max(axis=(1, 2)),
        "rising": means > np.roll(means, 1),
    }
    pl_df = pl.DataFrame(summary)

    # --- altair / Vega-Lite: interactive scatter -----------------------------
    # (built from pandas — polars.to_pandas() would need pyarrow, which we don't
    # want to pull in just for the demo.)
    chart = (
        alt.Chart(pd.DataFrame(summary))
        .mark_circle(size=90)
        .encode(
            x="time", y="mean_temp", color="max_temp", tooltip=["time", "mean_temp"]
        )
        .properties(title="Mean temperature over time", width=420, height=260)
        .interactive()
    )

    # --- plotly: interactive surface-ish scatter -----------------------------
    plotly_fig = px.scatter(
        x=np.arange(means.size),
        y=means,
        size=temps.max(axis=(1, 2)),
        color=means,
        title="Mean temperature (Plotly)",
        labels={"x": "time", "y": "mean temp"},
    )

    # --- bokeh + mpld3: built as ready-to-render HTML objects ----------------
    bokeh_plot = _bokeh_plot()
    mpl_hover = _mpld3_hover()

    # These all exist only to be inspected from the Console/Variables pane while
    # paused; reference them so the linter sees them used.
    _scene = (ds, pl_df, chart, plotly_fig, bokeh_plot, mpl_hover)
    judb.set_trace()  # <- paused here; every name above is live in this frame

    return {"peak": float(ds.temperature.max()), "n_rising": int(pl_df["rising"].sum())}


def simulate(steps: int) -> dict[str, float]:
    """Cheap diffusion so xarray/plotly have something non-trivial to show.

    Pure diffusion with periodic boundaries conserves total heat, so the spatial
    mean would be flat over time — dull. A steady cooling term plus a pulsing
    central heater make the mean actually rise and fall from step to step.
    """
    grid = np.zeros((steps, 24, 24))
    grid[0, 10:14, 10:14] = 10.0
    for t in range(1, steps):
        g = grid[t - 1]
        laplacian = (
            np.roll(g, 1, 0)
            + np.roll(g, -1, 0)
            + np.roll(g, 1, 1)
            + np.roll(g, -1, 1)
            - 4 * g
        )
        heater = np.zeros_like(g)
        heater[11:13, 11:13] = 6.0 * (1.0 + np.sin(t / 2.0))  # time-varying source
        grid[t] = g + 0.15 * laplacian - 0.05 * g + heater
    return build_scene(grid)


if __name__ == "__main__":
    print("result:", simulate(20))
