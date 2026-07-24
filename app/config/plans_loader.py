"""Optional YAML seed for first-time DB bootstrap (plans no longer required at runtime)."""

from pathlib import Path
from typing import Any

import yaml

_dir = Path(__file__).parent
_plans_path = _dir / "plans.yaml"
_example_path = _dir / "plans.example.yaml"


def load_yaml_seed() -> dict[str, Any] | None:
    path = _plans_path if _plans_path.exists() else _example_path
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return None
    return data
