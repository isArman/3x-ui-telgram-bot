"""The Mini App web server.

Serves the static Mini App and the JSON API on one port, next to the bot
process, so a single container serves both.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from app.utils.logger import logger
from app.web import admin_api, api  # noqa: F401  (registers the routes)
from app.web.api import auth_middleware
from app.web.responses import ok

STATIC_DIR = Path(__file__).parent / "static"


def create_app(bot) -> web.Application:
    """Build the aiohttp application for the Mini App."""
    # The receipt upload is the only body that can be big, so the limit is set
    # to the receipt size instead of aiohttp's 1 MiB default.
    app = web.Application(
        middlewares=[auth_middleware],
        client_max_size=api.MAX_RECEIPT_BYTES,
    )
    app["bot"] = bot

    app.add_routes(api.routes)
    app.add_routes(admin_api.routes)

    async def index(_request: web.Request) -> web.Response:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def health(_request: web.Request) -> web.Response:
        return ok({"status": "up"})

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_static("/assets/", STATIC_DIR, name="assets", show_index=False)

    return app


async def start_web_server(bot, host: str = "0.0.0.0", port: int = 8080):
    """Start the Mini App server and return its runner."""
    app = create_app(bot)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Mini App web server listening on %s:%s", host, port)
    return runner
