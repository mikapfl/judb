"""A small "scientific" workflow to show the debugger."""

import pandas as pd

import judb


def main() -> None:
    df = pd.DataFrame([[1, 2, 3], [2, 3, 4]])
    judb.set_trace()
    total = 0
    for i, row in df.iterrows():
        total += row.sum()


if __name__ == "__main__":
    main()
