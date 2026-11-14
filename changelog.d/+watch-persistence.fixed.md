Watch expressions now really do survive the next run of your program. They were
remembered in the browser, which scopes storage to judb's server port — and that
port is different every run, so in practice they came back after a page refresh
and nowhere else. They are now stored with the saved notebook.
