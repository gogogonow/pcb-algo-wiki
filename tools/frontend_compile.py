"""Run the packaged frontend_compile CLI from the repository root."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _load_main():
    module_path = SRC_ROOT / "tools" / "frontend_compile.py"
    spec = spec_from_file_location("_src_tools_frontend_compile", module_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load frontend_compile module from {module_path}"
        raise ImportError(msg)

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


main = _load_main()

if __name__ == "__main__":
    raise SystemExit(main())
