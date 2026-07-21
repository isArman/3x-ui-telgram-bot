import yaml
from pathlib import Path

_dir = Path(__file__).parent
_plans_path = _dir / "plans.yaml"
_example_path = _dir / "plans.example.yaml"

_path = _plans_path if _plans_path.exists() else _example_path
with open(_path, "r", encoding="utf-8") as _f:
    _data = yaml.safe_load(_f)

PLANS = _data["plans"]
PRICING = _data["pricing"]

PLAN_BY_ID = {p["id"]: p for p in PLANS}


def get_plan(plan_id: str):
    return PLAN_BY_ID.get(plan_id)
