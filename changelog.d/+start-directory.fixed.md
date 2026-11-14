judb now decides which project it is debugging from the directory the program
was *started* in, not the one it happens to be in at the first breakpoint. A
script that changes directory before pausing — or a test using
`monkeypatch.chdir(tmp_path)` under `pytest --pdbcls` — used to make judb look
for `[tool.judb]` settings in the wrong place.
