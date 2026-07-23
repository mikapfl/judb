Documented what works when the debuggee is not on the main thread — including
that a terminal Ctrl+C will not end a program paused in a worker thread, and
that two threads pausing at once is not supported yet.
