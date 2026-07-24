"""Shop plans and custom-plan pricing stored in DB."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PricingSettings, ShopPlan


def plan_to_dict(plan: ShopPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "days": plan.days,
        "traffic": plan.traffic_gb,
        "price": plan.price,
        "description": plan.description or "",
    }


async def get_pricing(session: AsyncSession) -> dict[str, int]:
    row = await get_pricing_settings(session)
    return {"per_day": int(row.per_day), "per_gb": int(row.per_gb)}


async def get_pricing_settings(session: AsyncSession) -> PricingSettings:
    result = await session.execute(
        select(PricingSettings).where(PricingSettings.id == 1)
    )
    row = result.scalar_one_or_none()
    if not row:
        row = PricingSettings(id=1, per_day=4000, per_gb=9000)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def set_pricing(
    session: AsyncSession, *, per_day: int | None = None, per_gb: int | None = None
) -> PricingSettings:
    row = await get_pricing_settings(session)
    if per_day is not None:
        row.per_day = int(per_day)
    if per_gb is not None:
        row.per_gb = int(per_gb)
    await session.commit()
    await session.refresh(row)
    return row


async def list_active_plans(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        select(ShopPlan)
        .where(ShopPlan.is_active.is_(True))
        .order_by(ShopPlan.sort_order, ShopPlan.id)
    )
    return [plan_to_dict(p) for p in result.scalars().all()]


async def list_all_plans(session: AsyncSession) -> list[ShopPlan]:
    result = await session.execute(
        select(ShopPlan).order_by(ShopPlan.sort_order, ShopPlan.id)
    )
    return list(result.scalars().all())


async def get_plan(
    session: AsyncSession, plan_id: str, *, active_only: bool = False
) -> dict[str, Any] | None:
    if not plan_id:
        return None
    result = await session.execute(select(ShopPlan).where(ShopPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        return None
    if active_only and not plan.is_active:
        return None
    return plan_to_dict(plan)


async def get_plan_row(session: AsyncSession, plan_id: str) -> ShopPlan | None:
    result = await session.execute(select(ShopPlan).where(ShopPlan.id == plan_id))
    return result.scalar_one_or_none()


async def count_plans(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(ShopPlan))
    return int(result.scalar_one())


async def create_plan(
    session: AsyncSession,
    *,
    plan_id: str,
    name: str,
    days: int,
    traffic_gb: int,
    price: int,
    description: str = "",
    sort_order: int | None = None,
) -> ShopPlan:
    if sort_order is None:
        sort_order = await count_plans(session)
    plan = ShopPlan(
        id=plan_id.strip(),
        name=name.strip(),
        days=int(days),
        traffic_gb=int(traffic_gb),
        price=int(price),
        description=(description or "").strip(),
        is_active=True,
        sort_order=int(sort_order),
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan


async def update_plan_fields(
    session: AsyncSession, plan: ShopPlan, **fields
) -> ShopPlan:
    for key, value in fields.items():
        if value is not None and hasattr(plan, key):
            setattr(plan, key, value)
    await session.commit()
    await session.refresh(plan)
    return plan


async def set_plan_active(
    session: AsyncSession, plan: ShopPlan, active: bool
) -> ShopPlan:
    plan.is_active = bool(active)
    await session.commit()
    await session.refresh(plan)
    return plan


async def bootstrap_plans_from_yaml(session: AsyncSession) -> int:
    """
    Seed shop_plans + pricing from plans.yaml / plans.example.yaml if empty.
    Returns number of plans inserted.
    """
    from app.config.plans_loader import load_yaml_seed

    existing = await count_plans(session)
    pricing = await get_pricing_settings(session)

    data = load_yaml_seed()
    if not data:
        return 0

    inserted = 0
    if existing == 0:
        for i, raw in enumerate(data.get("plans") or []):
            pid = str(raw.get("id") or "").strip()
            if not pid:
                continue
            session.add(
                ShopPlan(
                    id=pid,
                    name=str(raw.get("name") or pid),
                    days=int(raw.get("days") or 30),
                    traffic_gb=int(raw.get("traffic") or 10),
                    price=int(raw.get("price") or 0),
                    description=str(raw.get("description") or ""),
                    is_active=True,
                    sort_order=i,
                )
            )
            inserted += 1

    yaml_pricing = data.get("pricing") or {}
    # Only overwrite defaults if still at factory defaults and yaml has values
    if yaml_pricing:
        if pricing.per_day == 4000 and "per_day" in yaml_pricing:
            pricing.per_day = int(yaml_pricing["per_day"])
        if pricing.per_gb == 9000 and "per_gb" in yaml_pricing:
            pricing.per_gb = int(yaml_pricing["per_gb"])

    await session.commit()
    return inserted
