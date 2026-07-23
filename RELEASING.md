# Releasing judb

Publishing runs from `.github/workflows/release.yml`, which is **manual only**
(`workflow_dispatch`) — no push or tag ever triggers a release. It rebuilds and
re-verifies the artifacts, then uploads via PyPI **Trusted Publishing** (OIDC),
so no API token is ever stored in the repository.

Two pieces of setup have to be done **by hand, once**, before the first upload
will be accepted. Neither can be scripted from inside the repo: both live in
web UIs that require your account.

---

## One-time setup

### 1. Create the two GitHub environments

`Settings → Environments → New environment` in
<https://github.com/mikapfl/judb/settings/environments>. Create **both**, named
exactly:

| Environment | Purpose |
|---|---|
| `testpypi` | dry runs — leave unprotected so they stay friction-free |
| `pypi` | real releases — consider adding yourself as a **required reviewer**, so a release pauses for an explicit approval |

The names are not cosmetic: `release.yml` selects the environment from the
workflow's `target` input, and the trusted publisher registered in step 2 is
bound to a specific environment name. A mismatch means the upload is rejected.

### 2. Register a trusted publisher on each index

judb does not exist on either index yet, so both are registered as a **pending
publisher** (the form for a project that has never been published).

- Test-PyPI: <https://test.pypi.org/manage/account/publishing/>
- PyPI: <https://pypi.org/manage/account/publishing/>

Fill in exactly these values, changing only the environment per index:

| Field | Value |
|---|---|
| PyPI project name | `judb` |
| Owner | `mikapfl` |
| Repository name | `judb` |
| Workflow name | `release.yml` |
| Environment name | `testpypi` on Test-PyPI, `pypi` on PyPI |

You need an account on **each** index — they are separate services with separate
logins. Both were checked and the name `judb` was free on both at the time of
writing; if someone takes it in the meantime, the project name has to change in
`pyproject.toml` before registering.

---

## Releasing

1. **Land everything you want in the release**, each change carrying a
   `changelog.d/` fragment (see `changelog.d/README.md`).

2. **Preview the notes** and check they read well for users:

   ```bash
   make changelog-draft
   ```

   You do **not** bump the version or run `make changelog` — the release
   workflow does both (step 4). `make changelog-draft` is only a preview and
   consumes nothing.

3. **Dry run to Test-PyPI.** Actions → *Release* → *Run workflow* → `target:
   testpypi`, with the same `bump` you intend to release with.

   A dry run applies that bump *in memory only*, so it publishes the exact
   version the real release will produce — not the last released one, which the
   index would reject as a duplicate. Nothing is committed: the bump and the
   fragments are both still needed for the real run.

   Then verify the artifact installs from there in a throwaway venv:

   ```bash
   uv venv /tmp/judb-check && \
   uv pip install --python /tmp/judb-check/bin/python \
     --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ judb
   ```

   The extra index is needed because judb's dependencies live on real PyPI.

4. **Release for real.** Actions → *Release* → *Run workflow* → `target: pypi`,
   with the `bump` you want (`patch` / `minor` / `major`, or `none` to release
   the version already in `pyproject.toml`). If you configured a required
   reviewer, approve the run when it pauses.

   For a `pypi` target the workflow does the rest on its own:

   - bumps the version with `uv version` (updating `pyproject.toml` *and*
     `uv.lock`) and runs `towncrier build`, consuming the fragments, then
     **commits both back to the branch** you dispatched from as `release:
     v<version>`;
   - builds, tests and smoke-installs the artifacts *from that commit*, then
     uploads them;
   - pushes an annotated tag `v<version>` at that same commit;
   - opens a **draft** GitHub release titled `judb <version>`, with the new
     `CHANGELOG.md` section as the body and the wheel + sdist attached.

5. **Pull.** The release commit was made by CI, so your local branch is behind:

   ```bash
   git pull
   ```

6. **Publish the draft** at <https://github.com/mikapfl/judb/releases> once you
   have read it over. It is left as a draft deliberately — nothing is announced
   until you press the button.

## What the workflow checks for you

Before anything is uploaded, the build job:

- resolves the version (after any bump) and, for a `pypi` release, **refuses to continue if there
  is nothing to say** — no `changelog.d/` fragments *and* no existing
  `CHANGELOG.md` section for that version. Re-running a half-failed release is
  safe: if the section already exists, the changelog step is skipped rather
  than rebuilt;
- re-runs the Python test suite;
- runs `scripts/smoke_install.sh`, which builds the wheel *and* the sdist,
  installs each into a fresh virtualenv, and drives a real pause →
  plot-in-frame → continue round-trip against the installed package.

A broken artifact fails the release rather than reaching users.

## Notes

- **A version can never be re-uploaded.** Both indexes reject a re-used version
  even after deletion, so a bad release means bumping to the next patch.
- **The release workflow pushes one commit** (version bump + built changelog) to the branch
  it was dispatched from. If `main` ever gets branch protection requiring pull
  requests or reviews, that push will be rejected and the `prepare` job will
  fail — the fix is to allow the `github-actions` bot to bypass, or to go back
  to running `make changelog` by hand before dispatching.
- Test-PyPI prunes old files periodically; treat it as scratch space.
- Trusted Publishing needs `id-token: write`, which `release.yml` already grants
  to the publish job only.
