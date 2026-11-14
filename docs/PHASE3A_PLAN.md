# Phase 3a — Detailed Plan (saved & loadable debug cells)

*Companion to `IMPLEMENTATION_PLAN.md` §5, whose Phase 3a bullet lists three
kinds of saved cell and says "not yet decided what to implement". This doc
decides. Same conventions as `PHASE3_PLAN.md`: **[DECISION]** = taken and why,
**[OPEN]** = still needs a steer, each stage lists its **touchpoints** and an
**exit** check.*

## 0. The problem

**Today the notebook dies with the process.** You pause in `clean()`, build up
three cells that reshape and plot the frame's DataFrame, find the bug, fix it,
re-run — and type all three again. That is exactly the friction
`REQUIREMENT_ANALYSIS.md` says a notebook *does not* have ("iteration is hard,
because you have to copy + paste + change the code"), and judb currently
reintroduces it one layer up: the cells are editable and re-runnable, but only
until the debuggee exits.

The three kinds §5 sketched map onto that one problem at three distances:

| §5 calls it | It is really | Where it lives here |
| --- | --- | --- |
| "local" cells | the working notebook, surviving a restart | `$XDG_STATE_HOME/judb/notebooks/` |
| "project" cells | diagnostics worth committing for the next person | `<project root>/.judb/cells.py` |
| "setup" cells | your own `import numpy as np`, every session | `$XDG_CONFIG_HOME/judb/cells.py` |

## 1. What exploration turned up: localStorage cannot carry this

`server.py` binds **port 0** (`web.TCPSite(runner, self.host, 0)`), so every run
of the debuggee gets a fresh port — and therefore a fresh *origin*.
`localStorage` is partitioned by origin, so everything stored there survives a
**reload** (same tab, same port) but is gone on the **next run of the program**.

That makes B3's closing claim wrong: the watch list does *not* come back for "the
next run of the same program" (`PHASE3_PLAN.md` B3, *[DECISION] the browser owns
the list*). The mechanism was only ever a within-run one.

**[DECISION] the principle is corrected, not abandoned.** B3's instinct — one
owner, whole-list sync, no bidirectional settings protocol — was right; only the
storage *location* was wrong. Phase 3a establishes:

> **The browser owns the state; the backend owns the durability.**

The browser still decides what the cells and watches *are* and still re-sends the
whole list rather than deltas. The backend simply writes that list somewhere that
outlives a TCP port. Every consequence B3 liked (reconnect, refresh and the next
run all work through one path) finally becomes true instead of nearly true.

*Consequence for the watch list:* it moves to the same store in this wave, and
`judb-watches` in `localStorage` goes away. The theme stays where it is — a theme
that resets when you restart is an annoyance, not a lost afternoon's work, and
`prefers-color-scheme` gives it a sane default anyway. **[OPEN]** whether the
theme follows later.

## 2. Format — percent-format `.py`

**[DECISION] cells are stored as jupytext/VS Code/Spyder percent cells.**

```python
# %% describe the frame at="etl/clean.py::Cleaner.run"
df.describe()

# %% imports tags=["setup"]
import numpy as np
```

The marker line is `# %% <name> key=value…`: free text before the first `key=` is
the cell's name, and the `key=value` pairs are metadata. `tags` is the *standard*
notebook-metadata key jupytext already round-trips, so this is not a private
dialect.

Why not JSON (`.ipynb`-shaped) or TOML, the two obvious alternatives:

- The project tier is **committed to version control**, so its diff is read by
  humans in a pull request. A JSON cell array diffs appallingly; percent cells
  diff exactly like the Python they are.
- The file should be editable **without judb**. A `# %%` file opens as a notebook
  in VS Code, Spyder and PyCharm, and as plain Python everywhere else. Someone can
  write project cells in their editor and never learn judb's UI for it.
- It is valid Python, so it is lintable, greppable, and syntax-highlighted by
  everything. TOML with code in triple-quoted strings is none of those.

Unknown metadata keys are **preserved verbatim** in the two libraries, so judb
never eats another tool's metadata out of a file it shares — which holds because
judb *edits* a library on disk rather than rewriting it from the browser's copy
(§7).

## 3. Scoping — one location key, three rungs

Cells reference *variables*, and variables are scoped to functions. So the useful
cell set really is per-function — but per-project and per-file are just as real
("this project always wants `df.info()`"; "anything in `clean.py` wants this
plot"). **[DECISION] those are not three features but one, at three
specificities**, carried in the cell's `at=` metadata:

```
at="etl/clean.py::Cleaner.run"   shown while the selected frame is that function
at="etl/clean.py"                shown anywhere in that file
(absent)                         always available
```

The key is `"<path relative to the project root>::<co_qualname>"`.

- **Relative**, because an absolute path cannot survive a second checkout — and
  the committed tier is *for* other people's checkouts.
- **`co_qualname`** (3.11+; our floor is 3.13) because it gives `Class.method`
  for free, and a nested function keeps its enclosing context.
- A **module-level** frame keys to the file alone — a module frame's "function"
  is the file.
- A file **outside** the project root falls back to its absolute path. Only the
  machine-local tier can hold those anyway.

Matching is against the **selected frame**, not the whole stack: the selected
frame is already the subject of the Variables pane, the console and the watches,
and clicking a frame in the Call stack is how you say "that one".

**[DECISION] per-line association is excluded.** §5 floated "per line even?" —
but line numbers move on *every* edit of the code under debug, which is the code
you are, by definition, about to change. A per-line key would be wrong more often
than right, and wrong silently.

**A rename breaks the association, and that is survivable.** Nothing is deleted:
the cells simply stop surfacing on their own and stay listed, with their `at=`
visible, in the library drawer — which offers *re-key to here*. Fuzzy re-matching
is explicitly not attempted; a wrong guess about which function your cells belong
to is worse than a list you can see.

## 4. Presentation — tabs, and an explicit pin

Function-keyed cells mean the cell set changes as you step, which is the design's
real risk: a notebook that swaps underneath you loses your place, discards
outputs, and can make a half-typed cell vanish.

**[DECISION] the console grows tabs.**

```
┌─────────┬──────────────┬───────────┐
│ Scratch │ clean() ●    │ train()   │
└─────────┴──────────────┴───────────┘
```

- **Scratch** (`at: ""`) is tab 0, always present, never closed.
- A location tab exists only once it **has** cells. Landing in a function you
  have no cells for causes no churn at all.
- On `paused` / `frame_selected`, a tab matching the new location is activated.
  **Never out from under a focused editor**: if a cell editor has focus the tab
  is badged instead, and the switch happens at the next pause after blur.
- Other tabs stay visible and keep their outputs, so the swap is legible and
  reversible — which is the whole difference between this and replacing the
  notebook's contents.

**[DECISION] a cell acquires its key only by an explicit pin.** Cells start in
Scratch, keyed to nothing; *Pin to `clean()`* is a deliberate act on a cell or a
tab. The alternative (key every cell to the frame it was typed in) makes every
throwaway `print(x)` a saved artifact and starts moving the context on you from
the first keystroke. With the pin, judb's context never follows the frame until
you have said you want it to.

## 5. Trust — what may run itself

judb reads two files it did not write: the user's own library and the *project's*
committed one. Displaying or inserting either is harmless; running one is not.

**[DECISION] project cells are never auto-run.** Cloning a repository must not
hand it a code-execution path into your debugger. (The debuggee's own code runs,
of course — but that is the code you chose to debug, not a side file judb went
looking for.)

**Only `tags=["setup"]` cells in the user's own file auto-run**, which is the same
trust level as a shell rc. They run through the *ordinary* cell path — inserted
into Scratch, marked as setup, executed once at the first pause — so they get a
real `[n]`, real outputs and a real traceback if they fail, instead of a silent
side channel nobody can debug. They are re-materialised from the library each
session and excluded from the saved notebook, so they never accumulate.

## 6. Where the files live

```
~/.config/judb/cells.py                    user library   (authored; may auto-run)
<project root>/.judb/cells.py              project library (committed; never auto-runs)
~/.local/state/judb/notebooks/<slug>.py    the working notebook (auto-saved)
~/.local/state/judb/notebooks/<slug>.json  watches + active tab
```

`<slug>` is the project directory's name plus 8 hex of the SHA-256 of its
absolute path — readable in a directory listing, unique across checkouts.
"Project root" is the nearest enclosing `pyproject.toml`, reusing the walk
`config.py` already does for `[tool.judb]` (and stopping at the same place, for
the same reason), or the start directory when there is none. The walk starts
where the process was when judb was imported (under pytest: where pytest was
invoked), not where it stands at the first pause — a debuggee that changes
directory first would otherwise file its notebook under a directory nobody
debugs from again.

**[DECISION] judb auto-writes only outside the repository.** The working
notebook is saved continuously; doing that inside someone's checkout would mean
judb creating files they did not ask for, in a tree they may not own, that they
then have to gitignore. The repo is written **only** on an explicit *save to
project*. One rule, easy to state, and it makes `.judb/` unambiguous: everything
in it is meant to be committed.

## 7. Protocol (additive — mirror in `judb/protocol.py` *and* `frontend/src/protocol.ts`)

Client → server:

- `save_notebook` — `{cells: [{code, name, at, tags}], active}`. Whole-state, the
  `set_watches` idiom: the two ends cannot desync. **Flat**, exactly like the
  file — a cell's `at` is what groups it into a tab, so there is one shape to
  keep straight rather than two, and a hand-edited file loads with no
  translation.
- `library_add` — `{scope: "user"|"project", cells: [...]}`; `library_remove` —
  `{scope, cell}`; `library_rekey` — `{scope, cell, at}`. **Edits, not a
  whole-file replace** (which is what was first built): a library is a file the
  user also edits by hand, and the browser's copy dates from connect time, so
  replacing the file from it undid their edits — and "save this tab" replaced
  every cell sharing the tab's key, which for Scratch meant every un-keyed cell,
  setup cells included. The server re-reads the file and applies the one change.
- `set_watches` — unchanged on the wire; the server now *persists* what passes
  through it.

Server → client, pushed on websocket connect (no request needed):

- `notebook` — `{cells, active, watches}`, the restored machine-local state.
- `library` — `{user: [Cell], project: [Cell], paths: {...}}`, re-sent after any
  library edit, with an `error` string when the write failed.

`paused` / `frame_selected` gain an optional `location` string.

The client keeps ownership of the live watch list: it adopts the restored list
from `notebook` and hands it to the debugger with the usual `set_watches`, so the
debugger's watch handling is untouched.

## 8. Threading — storage rides the server thread

**[DECISION] cell storage is handled on the server thread, not through the
`inbound`/`outbound` queues.**

The queue seam exists for everything that touches the *debuggee* — and the
debuggee thread drains commands only while it is paused. A queued
`save_notebook` would therefore be lost exactly when the program runs off and
dies without pausing again, which is precisely the moment you most want your
cells back. Storage touches only disk: no frame, no shell, no cell execution.
`interrupt` already sets the precedent for bypassing the queue when the debuggee
thread is the wrong one to ask (`server.py`), so this is a second instance of an
existing rule rather than a new seam. Writes go through `asyncio.to_thread`; the
invariant "the server never executes cells" is untouched.

Everything that *does* need the debuggee stays where it was: `location` is
computed from a frame in `_frame_view`, and setup cells execute through
`execute_cell` on the debuggee thread like any other cell.

## 9. Stages

> **Status: all three stages are implemented** (branch `saved-cells`). The
> notes below are the plan as executed; two things moved during the work and are
> recorded where they belong: the wire carries a *flat* cell list rather than
> nested tabs (§7), and notebook adoption is guarded by "nothing on screen is
> ever overwritten" rather than by a once-per-session flag — a reconnect then
> needs no special case, because it simply finds nothing to do. A review after
> the first implementation then changed four things, each recorded in its
> section: libraries are edited on disk, never replaced (§7); the notebook is
> flushed before anything that can end the debuggee, not merely debounced; the
> project root is fixed at startup (§6); and a file-pinned tab follows the frame
> anywhere in its file, as §3's ladder always said it should.

### S1 — the notebook survives the process

- **`judb/cells.py`** (new): a frozen `Cell` (`code`, `name`, `at`, `tags`,
  `extra`), `parse`/`dumps`, `load`/`save` (atomic: temp file + `os.replace`),
  and `location_of(frame, root)`.
- **`judb/config.py`**: factor the `pyproject.toml` walk out of `_project_table`
  into `project_root()`; add `state_dir()` beside `user_config_path()`.
- **`judb/server.py`**: `save_notebook` and the library edits handled off the queue;
  `notebook` + `library` pushed on connect; the `set_watches` list persisted as
  it passes.
- **`frontend/src/lib/connection.svelte.ts`**: `tabs` (Scratch only for now),
  `cells` becomes the active tab's cells, `notebook` adopted when the notebook is
  untouched (never clobbering typing), debounced `save_notebook`,
  `WATCHES_KEY`/`loadWatches` removed in favour of the restored list.

*Exit:* run a cell, kill the debuggee, start it again — the cells are there.
Proven by a **second subprocess**, not a reconnect.

### S2 — location keys, tabs, pinning

- **`judb/debugger.py`**: `location` in `_frame_view`, via `cells.location_of`.
- **store**: `activateFor(location)` with the focused-editor rule, `pinCells(at)`,
  per-tab payloads.
- **`ConsolePane.svelte`** (+ a small tab-strip component): the tabs, a pin action
  on the tab, and one per cell in `NotebookCell.svelte`'s tools.

*Exit:* pin two cells to `clean()`, step out to the caller and back — the tab
returns, with its cells and their outputs.

### S3 — libraries, setup cells, docs

- **`CellLibrary.svelte`**: a popover from the console footer — **not** a sixth
  secondary pane; the layout already warns at three rows of them. Lists user and
  project cells, the ones matching here first, each with *insert*; plus *save this
  tab to project / to user* and *re-key to here*.
- Setup cells materialised into Scratch, run once, excluded from the save.
- Docs: this file kept current, `PHASE3_PLAN.md` and `IMPLEMENTATION_PLAN.md` §5
  updated, `CLAUDE.md` gains `judb/cells.py` and §5/§8's two rules, a README
  section, `changelog.d/` fragments.

## 10. Explicitly not in this phase

- **Per-line cell association** (§3).
- **Fuzzy re-matching after a rename** — *re-key to here* is the manual answer.
- **Location-keyed watches.** The argument from §3 applies to them word for word,
  but this wave only makes them durable. **[OPEN]** for a later pass.
- **A setting to trust project setup cells.** The answer for now is "never" (§5);
  if it is ever wanted it is a `[tool.judb]` key, not a prompt.
- **Syncing the theme** (§1).
