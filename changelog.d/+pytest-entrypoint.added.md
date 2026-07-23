`pytest --pdb --pdbcls=judb:Debugger` drops you into the browser UI paused at a
failing test, with the console live in the frame that blew up. `--trace` (break
at the start of each test) and `breakpoint()` inside a test work too.
