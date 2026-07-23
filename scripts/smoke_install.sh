#!/usr/bin/env bash
# Build wheel + sdist and verify a *fresh* install works end to end — the
# packaging & install story from PHASE3_PLAN.md A1.
#
# Installing the built artifacts needs no Node: the wheel bakes in the frontend
# bundle, and the sdist ships the pre-built bundle (its build hook finds no
# frontend/ to rebuild, so pnpm is never invoked). We install BOTH artifacts
# into throwaway venvs and run scripts/smoke_install.py against each.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

echo "==> building wheel + sdist (uv build)"
rm -rf dist
uv build  # builds the sdist, then the wheel *from* that sdist

smoke_one() {  # $1 = artifact path/glob to install into a fresh venv
    local artifact
    artifact=$(ls $1)
    local venv
    venv="$(mktemp -d)/venv"
    echo "==> fresh venv install + round-trip: $(basename "$artifact")"
    uv venv --quiet "$venv"
    uv pip install --quiet --python "$venv/bin/python" "$artifact"
    # Run from / so the source checkout is not on sys.path — this must exercise
    # the *installed* package, not the repo.
    (cd / && "$venv/bin/python" "$ROOT/scripts/smoke_install.py")
    rm -rf "$(dirname "$venv")"
}

smoke_one "dist/judb-*.whl"
smoke_one "dist/judb-*.tar.gz"
echo "==> smoke OK: wheel and sdist both install cleanly and round-trip"
