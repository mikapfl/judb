**Your debug cells now outlive the program.** The console is saved as you type
and comes back on the next run — same cells, same watch expressions — so fixing
a bug and re-running no longer means retyping the three cells you built up to
diagnose it. Cells are stored as ordinary `# %%` percent cells, the format
jupytext, VS Code and Spyder already read, so the file opens as a notebook in
your own editor.

Cells can be **pinned to where they belong**: pin one to the function you are
paused in and the console grows a tab for it, which comes back to the front
whenever you land in that function again — and stays out of your way when you
are somewhere else, or when you are in the middle of typing. **Saved cells**
(the ☰ button above the console) keeps two libraries: your own, available in
every project, and one committed with the project, so anyone debugging it gets
the same diagnostics. Cells tagged `setup` in *your* library — the
`import numpy as np` you always want — run themselves once a session; cells
that came with a project never run by themselves.
