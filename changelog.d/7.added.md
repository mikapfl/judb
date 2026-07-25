**Open any file, and break in code you haven't reached yet.** The Source pane
can now show a file no frame is in: type a path into *Open file…* (or pick one
of the files judb already knows), set a breakpoint in the gutter as usual, and
`continue` — the debuggee stops there the first time it runs that line. Rows in
the Breakpoints pane and frames in the Exception pane that have already unwound
navigate to their file the same way. While you are browsing, the pane says so
plainly and offers a *Back to frame* button, so an unhighlighted file can never
be mistaken for the paused one.
