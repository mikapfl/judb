"""Phase 2 demo: run this, a browser tab opens at the paused frame.

    uv run python scripts/demo_p2.py

Then in the Console pane (runs in the paused frame) try, e.g.:

    df                                          # rich HTML table
    import matplotlib.pyplot as plt; plt.plot(signal)   # inline PNG
    df.describe()
    signal.mean()

Use Continue / Next / Step / Return / Quit in the toolbar; the Call stack pane
shows the nested frames (analyze → transform), Variables lists the locals.
"""

import numpy as np
import pandas as pd

import judb


def transform(signal: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame({"t": np.arange(signal.size), "signal": signal})
    judb.set_trace()  # <- pauses here; `signal` and `df` are live in the frame
    df["smoothed"] = df["signal"].rolling(5, min_periods=1).mean()
    return df


def analyze() -> float:
    signal = np.sin(np.linspace(0.0, 6.28, 100)) + np.random.default_rng(0).normal(
        0, 0.1, 100
    )
    df = transform(signal)
    return float(df["smoothed"].max())


if __name__ == "__main__":
    print("peak:", analyze())
