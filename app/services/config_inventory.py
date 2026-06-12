from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PlanConfig


async def count_available(session: AsyncSession, plan_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(PlanConfig)
        .where(PlanConfig.plan_id == plan_id, PlanConfig.is_assigned.is_(False))
    )
    return result.scalar_one()


async def assign_config(
    session: AsyncSession,
    plan_id: str,
    order_id: int,
) -> Optional[PlanConfig]:
    result = await session.execute(
        select(PlanConfig)
        .where(PlanConfig.plan_id == plan_id, PlanConfig.is_assigned.is_(False))
        .order_by(PlanConfig.created_at.asc())
        .limit(1)
    )
    config = result.scalar_one_or_none()
    if not config:
        return None

    config.is_assigned = True
    config.order_id = order_id
    config.assigned_at = datetime.utcnow()
    await session.flush()
    return config


async def add_config(
    session: AsyncSession,
    plan_id: str,
    config_text: str,
    admin_id: int,
) -> PlanConfig:
    entry = PlanConfig(
        plan_id=plan_id,
        config_text=config_text.strip(),
        created_by=admin_id,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry
