import yaml
from pathlib import Path

_plans_path = Path(__file__).parent / "plans.yaml"
with open(_plans_path, "r", encoding="utf-8") as _f:
    _data = yaml.safe_load(_f)

PLANS = _data["plans"]
PRICING = _data["pricing"]

PLAN_BY_ID = {p["id"]: p for p in PLANS}


def get_plan(plan_id: str):
    return PLAN_BY_ID.get(plan_id)
