**Watch expressions.** A new **Watch** pane (under Variables) keeps a list of
expressions you care about — `df.shape`, `total / n`, `arr.mean()` — and
re-evaluates them in the selected frame on every pause, frame change and console
cell run, so you can watch a value change as you step. Unfold a row to see the
full value the way the console renders it (a watched DataFrame shows its HTML
table). An expression that doesn't resolve in the current frame reports its error
on its own row, leaving the rest of the pane intact, and the list is remembered
across a refresh and the next run of the same program.
