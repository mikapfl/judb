"""Read the release version and its ``CHANGELOG.md`` section.

Used by ``.github/workflows/release.yml`` to name the tag and fill the draft
GitHub release, so what appears there is exactly what shipped in the changelog.

    python scripts/release_notes.py version   # -> 0.1.0
    python scripts/release_notes.py notes     # -> that version's section body

Both subcommands fail loudly rather than guessing: releasing a version with no
changelog section almost certainly means ``make changelog`` was not run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_version() -> str:
    """The single source of truth: ``__version__`` in ``judb/__init__.py``."""
    text = (ROOT / "judb" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("could not find __version__ in judb/__init__.py")
    return match.group(1)


def read_notes(version: str) -> str:
    """The body of the ``## [<version>] …`` section, up to the next section."""
    text = (ROOT / "CHANGELOG.md").read_text()
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        raise SystemExit(
            f"CHANGELOG.md has no section for {version}. Run `make changelog` "
            "and commit the result before releasing."
        )
    body = match.group(1).strip()
    if not body:
        raise SystemExit(f"the CHANGELOG.md section for {version} is empty.")
    return body


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"version", "notes"}:
        raise SystemExit(__doc__)
    version = read_version()
    print(version if sys.argv[1] == "version" else read_notes(version))


if __name__ == "__main__":
    main()
