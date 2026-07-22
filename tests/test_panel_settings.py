import pytest

from app.database.models import PanelSettings
from app.services.panel_settings import (
    PROVISIONING_AUTO,
    PROVISIONING_MANUAL,
    get_panel_password,
    get_panel_settings,
    is_auto_provisioning_ready,
    set_panel_password,
)


@pytest.mark.asyncio
async def test_is_auto_provisioning_ready_requires_all_fields():
    panel = PanelSettings(
        id=1,
        provisioning_mode=PROVISIONING_AUTO,
        is_verified=True,
        panel_url="https://panel.example.com",
        panel_username="admin",
        subscription_base_url="https://panel.example.com/sub/",
        selected_inbound_ids="[1,2]",
    )
    set_panel_password(panel, "secret")
    assert is_auto_provisioning_ready(panel) is True


@pytest.mark.asyncio
async def test_is_auto_provisioning_ready_manual_mode():
    panel = PanelSettings(
        id=1,
        provisioning_mode=PROVISIONING_MANUAL,
        is_verified=True,
        panel_url="https://panel.example.com",
        panel_username="admin",
        subscription_base_url="https://panel.example.com/sub/",
        selected_inbound_ids="[1]",
    )
    set_panel_password(panel, "secret")
    assert is_auto_provisioning_ready(panel) is False


@pytest.mark.asyncio
async def test_panel_password_encryption():
    panel = PanelSettings(id=1)
    set_panel_password(panel, "my-panel-password")
    assert panel.panel_password != "my-panel-password"
    assert get_panel_password(panel) == "my-panel-password"


@pytest.mark.asyncio
async def test_get_panel_settings_creates_default_row():
    from app.database.session import AsyncSessionLocal, init_db

    await init_db()
    async with AsyncSessionLocal() as session:
        row = await get_panel_settings(session)
        assert row.id == 1
        assert row.provisioning_mode == PROVISIONING_MANUAL
