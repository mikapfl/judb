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

3. **Set the version.** One line, in `judb/__init__.py`:

   ```python
   __version__ = "0.1.0"
   ```

   That is the single source of truth — hatchling builds from it and towncrier
   titles the release with it.

4. **Build the changelog**, which consumes the fragments:

   ```bash
   make changelog
   ```

5. **Commit and push** the version bump plus `CHANGELOG.md` and the removed
   fragments. Let CI go green on `main`.

6. **Dry run to Test-PyPI.** Actions → *Release* → *Run workflow* → `target:
   testpypi`. Then verify the artifact installs from there in a throwaway venv:

   ```bash
   uv venv /tmp/judb-check && \
   uv pip install --python /tmp/judb-check/bin/python \
     --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ judb
   ```

   The extra index is needed because judb's dependencies live on real PyPI.

7. **Release for real.** Actions → *Release* → *Run workflow* → `target: pypi`.
   If you configured a required reviewer, approve the run when it pauses.

8. **Tag the release** so the changelog links resolve:

   ```bash
   git tag -a v0.1.0 -m "judb 0.1.0" && git push origin v0.1.0
   ```

## What the workflow checks for you

Before anything is uploaded, the build job re-runs the Python test suite and
`scripts/smoke_install.sh`, which builds the wheel *and* the sdist, installs
each into a fresh virtualenv, and drives a real pause → plot-in-frame →
continue round-trip against the installed package. A broken artifact fails the
release rather than reaching users.

## Notes

- **A version can never be re-uploaded.** Both indexes reject a re-used version
  even after deletion, so a bad release means bumping to the next patch.
- Test-PyPI prunes old files periodically; treat it as scratch space.
- Trusted Publishing needs `id-token: write`, which `release.yml` already grants
  to the publish job only.
