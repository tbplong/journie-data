from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def pytest_configure(config: object) -> None:
    # Tests also run from a fresh checkout where the package is not installed editable.
    assert FIXTURES.is_dir()
