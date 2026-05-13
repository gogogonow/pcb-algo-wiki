from pathlib import Path
import re
import shutil
import sys

import pytest


src = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(src))

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
