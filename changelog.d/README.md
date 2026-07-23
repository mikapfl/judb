# Changelog fragments

One file per user-visible change, collated into `CHANGELOG.md` at release time
by [towncrier](https://towncrier.readthedocs.io/). Fragments are separate files
so branches in flight never conflict over the changelog.

Name a fragment `<id>.<type>.md`:

- `<id>` is the issue or PR number — or `+something` for a change with no issue
  (e.g. `+pty-ctrl-c.fixed.md`).
- `<type>` is one of `added`, `changed`, `fixed`, `removed`, `docs`.

Write one or two sentences in the user's language — what changed for *them*, not
which function moved. Example, in `changelog.d/+reconnect.fixed.md`:

```markdown
Refreshing the browser tab while paused now restores the debugger UI instead of
showing a blank page.
```

Useful commands:

- `make changelog-draft` — preview the rendered notes without consuming fragments.
- `make changelog` — build `CHANGELOG.md` and delete the fragments (release time).

If a change genuinely has nothing to tell users — a refactor, a CI tweak — add an
empty `<pr-number>.misc.md`. It satisfies the CI check and renders only as a
reference under *Misc*, with no prose.
