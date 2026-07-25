"""Break-on-exception demo: a crash you can actually debug in the browser.

Run it **under `python -m judb`** so an uncaught exception drops you into
post-mortem instead of just printing a traceback and exiting:

    uv run python -m judb scripts/demo_crash.py

It stops on entry first — hit **Continue** in the toolbar to let the program
run. It builds a small sales DataFrame, then crashes while computing a
per-region average order value for a region that has no orders (a division by
zero). judb catches the crash and re-pauses on the failing frame:

  * the **Exception** pane (bottom-right) shows ``ZeroDivisionError`` and the
    full traceback;
  * the **Console** pane runs *in that frame*, so you can inspect what went
    wrong on the real objects:

        region                  # 'north' — the region with no orders
        rows                    # the empty DataFrame slice
        len(rows)               # 0  -> that is the division by zero
        df                      # the whole sales table (rich HTML)
        df['region'].unique()   # the regions that *do* have rows

The rest of ``main`` (printing the report, finding the top region) is the "and
then it wanted to do something else" that the crash cut short — it never runs.

(For contrast: a plain ``python scripts/demo_crash.py`` just dies with a
terminal traceback. The ``-m judb`` wrapper is what turns the crash into a
paused, inspectable frame.)
"""

import pandas as pd

# 'north' is deliberately absent from the data below — that is the bug this
# demo walks into when it asks for north's average.
REGIONS = ["north", "south", "east", "west"]


def load_sales() -> pd.DataFrame:
    """A tiny orders table — note there are no 'north' rows."""
    return pd.DataFrame(
        {
            "region": ["south", "south", "east", "west", "west", "east"],
            "order_value": [120, 80, 200, 50, 75, 300],
        }
    )


def average_order_value(df: pd.DataFrame, region: str) -> float:
    """Mean order value for one region — crashes for a region with no rows."""
    rows = df[df["region"] == region]
    return sum(rows["order_value"]) / len(rows)  # len == 0 for 'north' -> boom


def build_report(df: pd.DataFrame) -> pd.DataFrame:
    """Average order value per region (raises inside the comprehension)."""
    return pd.DataFrame(
        {
            "region": REGIONS,
            "avg_order_value": [average_order_value(df, r) for r in REGIONS],
        }
    )


def main() -> None:
    df = load_sales()
    print(f"Loaded {len(df)} orders across {df['region'].nunique()} regions")

    report = build_report(df)  # <- crashes here on the 'north' region

    # Everything below is the "and then do something else" that never runs,
    # because build_report raised on the way in:
    print(report.to_string(index=False))
    top = report.sort_values("avg_order_value").iloc[-1]
    print(f"Top region: {top['region']} (avg {top['avg_order_value']:.0f})")


if __name__ == "__main__":
    main()
