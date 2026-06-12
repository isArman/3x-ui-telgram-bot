from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models import PanelConfig, ProvisionJob
from app.xui.client import XUIClient


@dataclass
class EffectivePanelConfig:
    url: str
    public_url: str
    username: str
    password: str
    inbound_id: int
    auto_create: bool
    provision_mode: str

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)

    @property
    def can_auto_create(self) -> bool:
        return self.auto_create and self.is_configured

    @property
    def is_remote_mode(self) -> bool:
        return self.provision_mode == "remote"

    @property
    def subscription_base_url(self) -> str:
        return (self.public_url or self.url).rstrip("/")


def build_xui_client(
    config: EffectivePanelConfig,
    api_url: Optional[str] = None,
) -> XUIClient:
    return XUIClient(
        base_url=(api_url or config.url).rstrip("/"),
        username=config.username,
        password=config.password,
        inbound_id=config.inbound_id,
        public_base_url=config.subscription_base_url,
    )


async def get_or_create_panel_config(session: AsyncSession) -> PanelConfig:
    result = await session.execute(select(PanelConfig).where(PanelConfig.id == 1))
    panel = result.scalar_one_or_none()

    if panel:
        return panel

    panel = PanelConfig(
        id=1,
        url=settings.XUI_URL,
        public_url=settings.XUI_PUBLIC_URL or settings.XUI_URL,
        username=settings.XUI_USERNAME,
        password=settings.XUI_PASSWORD,
        inbound_id=settings.XUI_INBOUND_ID,
        auto_create=bool(settings.XUI_USERNAME and settings.XUI_PASSWORD),
        provision_mode=settings.PROVISION_MODE,
    )
    session.add(panel)
    await session.commit()
    await session.refresh(panel)
    return panel


async def get_effective_panel_config(session: AsyncSession) -> EffectivePanelConfig:
    panel = await get_or_create_panel_config(session)
    url = (panel.url or "").strip()
    public_url = (panel.public_url or url).strip()
    provision_mode = panel.provision_mode or settings.PROVISION_MODE or "direct"

    return EffectivePanelConfig(
        url=url,
        public_url=public_url,
        username=panel.username.strip(),
        password=panel.password,
        inbound_id=panel.inbound_id,
        auto_create=panel.auto_create,
        provision_mode=provision_mode,
    )


async def create_provision_job(
    session: AsyncSession,
    payment_id: int,
    order_id: int,
    user_id: int,
    days: int,
    traffic_gb: int,
    admin_id: int,
) -> ProvisionJob:
    job = ProvisionJob(
        payment_id=payment_id,
        order_id=order_id,
        user_id=user_id,
        days=days,
        traffic_gb=traffic_gb,
        admin_id=admin_id,
        status="pending",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def claim_next_provision_job(session: AsyncSession) -> Optional[ProvisionJob]:
    result = await session.execute(
        select(ProvisionJob)
        .where(ProvisionJob.status == "pending")
        .order_by(ProvisionJob.created_at.asc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if not job:
        return None

    job.status = "processing"
    job.started_at = datetime.utcnow()
    await session.commit()
    await session.refresh(job)
    return job


async def provision_vpn_account(
    session: AsyncSession,
    user_id: int,
    days: int,
    traffic_gb: int,
) -> tuple[Optional[dict], Optional[str]]:
    """Create a VPN client directly on 3x-ui (direct mode only)."""
    config = await get_effective_panel_config(session)
    if not config.can_auto_create:
        return None, "ساخت خودکار غیرفعال است یا پنل تنظیم نشده."

    if config.is_remote_mode:
        return None, "در حالت remote باید worker روی سرور ایران job را پردازش کند."

    if not config.url:
        return None, "URL پنل برای حالت direct تنظیم نشده است."

    client = build_xui_client(config)
    email = f"tg_{user_id}"

    result = await client.add_client(
        email=email,
        traffic_gb=traffic_gb,
        expire_days=days,
    )
    if not result:
        return None, "ساخت کلاینت در پنل 3x-ui ناموفق بود."

    return result, None
