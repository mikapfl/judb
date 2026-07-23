"""Hatchling build hook: build the Svelte SPA into ``judb/static/index.html``.

The built bundle is intentionally *not* committed to git (it would pollute every
diff). Instead it is generated on demand:

- for local dev/tests, via ``make frontend`` (``cd frontend && pnpm run build``);
- at package-build time, by this hook — so ``uv build`` / ``pip install .`` from a
  source checkout produces a working wheel without a separate manual step.

When building from an sdist (which has no ``frontend/`` source tree but *does*
ship the pre-built ``index.html`` — see the ``artifacts`` config in
``pyproject.toml``), there is nothing to build and the existing file is used.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface[Any]):
    PLUGIN_NAME = "frontend-build"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        frontend = root / "frontend"
        bundle = root / "judb" / "static" / "index.html"

        if not frontend.is_dir():
            # Building from an sdist: no sources to build; the pre-built bundle
            # must already be present (packaged via `artifacts` in pyproject).
            if not bundle.is_file():
                msg = (
                    f"{bundle} is missing and there is no frontend/ to build it "
                    "from. This sdist appears to be incomplete."
                )
                raise RuntimeError(msg)
            return

        pnpm = shutil.which("pnpm")
        if pnpm is None:
            if version == "editable":
                # An *editable* install (`uv sync`, `pip install -e .`) does not
                # ship the bundle, so Node is not actually required to get one.
                # Refusing here would force Node onto every environment that
                # merely wants to import judb or run the Python tests — including
                # CI jobs that deliberately carry no Node. Warn and carry on; the
                # server prints a "run `make frontend`" error if the bundle is
                # genuinely missing when a browser asks for it.
                self.app.display_warning(
                    "judb: pnpm not found — skipping the frontend build for this "
                    "editable install. Run `make frontend` before serving the UI."
                )
                return
            # A real wheel/sdist must never ship without a bundle.
            msg = (
                "pnpm is required to build the judb frontend bundle but was not "
                "found on PATH. Run `corepack enable`, then `make frontend` "
                "(see CLAUDE.md / PHASE2_STACK.md)."
            )
            raise RuntimeError(msg)

        self.app.display_info("judb: building frontend bundle (pnpm run build)…")
        subprocess.run([pnpm, "run", "build"], cwd=frontend, check=True)

        if not bundle.is_file():
            msg = f"frontend build finished but {bundle} was not produced"
            raise RuntimeError(msg)
