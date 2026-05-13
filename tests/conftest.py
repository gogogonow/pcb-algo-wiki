from pathlib import Path
import re
import shutil
import sys

import pytest

repo_root = Path(__file__).resolve().parents[1]
src = repo_root / "src"
sys.path[:0] = [str(src), str(repo_root)]

ARTIFACTS_ROOT = Path(__file__).resolve().parents[1] / ".test-artifacts"


@pytest.fixture
def scratch_dir(request: pytest.FixtureRequest) -> Path:
    path = ARTIFACTS_ROOT / re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    yield path
    if path.exists():
        shutil.rmtree(path)
