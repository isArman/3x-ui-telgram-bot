from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models import PanelConfig
from app.xui.client import XUIClient


@dataclass
class EffectivePanelConfig:
    url: str
    username: str
    password: str
    inbound_id: int
    auto_create: bool

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.username and self.password)

    @property
    def can_auto_create(self) -> bool:
        return self.auto_create and self.is_configured


def build_xui_client(config: EffectivePanelConfig) -> XUIClient:
    return XUIClient(
        base_url=config.url,
        username=config.username,
        password=config.password,
        inbound_id=config.inbound_id,
    )


async def get_or_create_panel_config(session: AsyncSession) -> PanelConfig:
    result = await session.execute(select(PanelConfig).where(PanelConfig.id == 1))
    panel = result.scalar_one_or_none()

    if panel:
        return panel

    panel = PanelConfig(
        id=1,
        url=settings.XUI_URL,
        username=settings.XUI_USERNAME,
        password=settings.XUI_PASSWORD,
        inbound_id=settings.XUI_INBOUND_ID,
        auto_create=bool(settings.XUI_URL and settings.XUI_USERNAME and settings.XUI_PASSWORD),
    )
    session.add(panel)
    await session.commit()
    await session.refresh(panel)
    return panel


async def get_effective_panel_config(session: AsyncSession) -> EffectivePanelConfig:
    panel = await get_or_create_panel_config(session)
    return EffectivePanelConfig(
        url=panel.url.strip(),
        username=panel.username.strip(),
        password=panel.password,
        inbound_id=panel.inbound_id,
        auto_create=panel.auto_create,
    )


async def provision_vpn_account(
    session: AsyncSession,
    user_id: int,
    days: int,
    traffic_gb: int,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Create a VPN client on 3x-ui panel.
    Returns (result_dict, error_message).
    """
    config = await get_effective_panel_config(session)
    if not config.can_auto_create:
        return None, "ساخت خودکار غیرفعال است یا پنل تنظیم نشده."

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
