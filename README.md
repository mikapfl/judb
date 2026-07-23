# judb

A browser-based visual Python debugger for datascience - debug any Python module
or function, with the full power of jupyter notebooks.

`judb` uses `pudb`-style stepping with a
notebook-style rich console that executes cells **in the currently-paused stack
frame**. Plot and inspect a paused frame's real objects — DataFrames, arrays,
xarray Datasets, matplotlib figures — the way you would in a notebook.

## Status

Early prototype.

## Try it out

Requires Python ≥ 3.13.

```bash
pip install judb          # or, in a checkout: uv sync
```

Drop `judb.set_trace()` where you want to pause:

```python
import judb

def analyze(df):
    judb.set_trace()      # opens a browser tab, paused on the next line
    return df.describe()
```

Run your script normally. A browser tab opens at the paused frame with four
panes — **Source**, a **notebook console**, **Variables**, and the **Call
stack**. Step with the toolbar (Continue / Next / Step / Return); in the console,
type Python that runs *in the paused frame*:

```python
df                        # rich HTML table
df["x"].rolling(5).mean() # any expression, evaluated in-frame
import matplotlib.pyplot as plt; plt.plot(df["x"])   # inline figure
```

The console is a real notebook: cells are editable and re-runnable, and can be
added, deleted, and reordered. You can also wire judb up as the `breakpoint()`
hook, no code change needed:

```bash
PYTHONBREAKPOINT=judb.set_trace python your_script.py
```

Or run a whole script or module under judb without touching it (stops at the
first line):

```bash
python -m judb your_script.py [args...]
python -m judb -m your.pipeline.module [args...]
```

## Debugging a failing test

Point pytest's post-mortem debugger at judb, and a failing test drops you into
the browser UI **paused at the failure**, with the console live in that frame:

```bash
pytest --pdb --pdbcls=judb:Debugger
```

Inspect the assertion's operands, plot the offending array, poke at locals — all
in the frame where the test blew up. Hit Continue to move on.

With `--pdbcls=judb:Debugger` set, pytest's other entry points reach judb too —
`--trace` breaks at the first line of every test, and a `breakpoint()` inside a
test opens the UI there:

```bash
pytest --trace --pdbcls=judb:Debugger      # break at the start of each test
pytest --pdbcls=judb:Debugger              # honour breakpoint() in a test
```

Prefer a ready-made demo? From a checkout:

```bash
uv run python scripts/demo_p2.py     # a small paused frame with an array to plot

uv sync --extra example              # heavier viz libs (xarray/plotly/bokeh/altair/polars)
uv run python scripts/demo_rich.py   # a spread of rich objects to inspect
```

## Interactive plots (zoom & pan)

By default a plot renders as a static inline PNG. For **interactive** figures —
zoom, pan, the full matplotlib toolbar, live while paused — run this once in the
console, then plot as usual:

```python
%matplotlib judb                     # judb's interactive backend
import matplotlib.pyplot as plt
plt.plot(signal)                     # a live, zoomable/pannable canvas
```

Switch back to static images at any time with `%matplotlib inline`. Standard
IPython magics work too (`%timeit`, `%who`, `%%time`, `%matplotlib inline`, …).

Under the hood this is matplotlib's own WebAgg engine (the same one behind
`%matplotlib notebook`) driven over judb's connection — **no Jupyter kernel
required**. Interactivity is live whenever the debuggee is paused and freezes on
Continue, since the figure lives in the paused frame.

## Threads and processes

judb debugs **one paused frame at a time**. Where that frame lives changes what
works:

| Where `set_trace()` runs | Status |
|---|---|
| The main thread (a normal script, `python -m judb`, pytest) | Fully supported |
| A single worker thread | Supported, with two caveats below |
| Two threads pausing at the same time | **Not supported** — see below |
| A child process (`multiprocessing`, `fork`, `spawn`) | Supported — each process gets its own UI |

**A single worker thread** pauses, shows its frame, and runs console cells in it
exactly as the main thread does. Two things degrade, both because only the main
thread can receive signals:

- **Interrupt** falls back from a real `SIGINT` to Python's async-exception API,
  which lands at the next bytecode. It still stops a Python loop, but it cannot
  break out of a blocking call like `time.sleep` until that call returns.
- **Ctrl+C in the terminal will not end the program.** The `KeyboardInterrupt`
  is delivered to the main thread, while the paused worker keeps waiting; if it
  is a non-daemon thread the process will not exit. Use the UI's Quit, or kill
  the process.

**Two threads pausing concurrently** is not supported yet (real multi-thread
debugging is planned for a later phase). It does not crash, but: only the most
recent pause is visible, the other paused thread is invisible while still
blocked, and each Continue releases an arbitrary one of them. If your debuggee
is multi-threaded, set a breakpoint that only one thread can reach.

**Child processes** each start their own server on their own port and print
their own URL, so you get one browser tab per paused process. This holds for
`fork` too: a forked child does not reuse its parent's server, since the threads
running it do not survive the fork.

## License

Copyright 2026 Mika Pflüger. Licensed under the [Apache License 2.0](LICENSE)
(see also [`NOTICE`](NOTICE)).

judb's debugger architecture and design borrow heavily from
[PuDB](https://github.com/inducer/pudb) (MIT/X Consortium license). PuDB's
license and attribution notice are reproduced in
[`licenses/pudb-LICENSE.txt`](licenses/pudb-LICENSE.txt). Rich-output CSS is
partly derived from [Project Jupyter](https://jupyter.org/) (BSD-3-Clause; see
[`licenses/jupyter-LICENSE.txt`](licenses/jupyter-LICENSE.txt)). The interactive
plotting backend reuses [Matplotlib](https://matplotlib.org/)'s WebAgg engine and
serves its client JS and toolbar assets (BSD-compatible license; see
[`licenses/matplotlib-LICENSE.txt`](licenses/matplotlib-LICENSE.txt)).
