"""Suite-wide isolation of the places judb writes.

Since Phase 3a judb persists the working notebook and the watch list under
``$XDG_STATE_HOME`` (see `judb.cells`), keyed by project root — and the project
root of an in-process test *is* this checkout. Without this fixture the suite
would read and overwrite the developer's own saved cells, and one test's watches
would leak into the next. Point both XDG roots at a temp directory for the whole
session; subprocess-based tests inherit it through the environment.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from judb import config


@pytest.fixture(autouse=True, scope="session")
def _isolated_xdg(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    root: Path = tmp_path_factory.mktemp("xdg")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("XDG_STATE_HOME", str(root / "state"))
        patch.setenv("XDG_CONFIG_HOME", str(root / "config"))
        config.reset()
        yield
        config.reset()
