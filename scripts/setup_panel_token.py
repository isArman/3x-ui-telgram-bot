#!/usr/bin/env python3
"""
Mint a 3x-ui API token for the bot and store it (encrypted) in the database.

Run this inside the bot container so the app's own encryption is used:

    docker compose exec bot python scripts/setup_panel_token.py [name]

The panel only returns the token's plaintext once — it stores a hash — so it is
written to the database immediately. Afterwards the bot authenticates with
``Authorization: Bearer <token>``, which skips the panel's CSRF check and the
session login.
"""

from __future__ import annotations

import asyncio
import sys

from app.database.session import AsyncSessionLocal
from app.services.panel_settings import (
    get_panel_password,
    get_panel_settings,
    set_panel_api_token,
)
from app.xui.client import XUIClient


async def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "vpn-telegram-bot"

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        if not ps.panel_url or not ps.panel_username:
            print("panel is not configured yet — run the panel setup in the bot first")
            return 1
        url = ps.panel_url
        username = ps.panel_username
        password = get_panel_password(ps)

    if not password:
        print("no panel password stored — the bot needs it to mint a token")
        return 1

    async with XUIClient(url, username, password) as client:
        token = await client.create_api_token(name)

    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        set_panel_api_token(ps, token)
        await session.commit()

    print(f"API token '{name}' created and stored")
    print("restart the bot to use it")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
