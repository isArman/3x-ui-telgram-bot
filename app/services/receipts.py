"""Upload a payment receipt (sent from the Mini App) to Telegram.

Telegram keeps the file, so we only need to store the resulting ``file_id``.
"""

from __future__ import annotations

import httpx

from app.config.settings import settings
from app.utils.logger import logger

_API = "https://api.telegram.org"


async def upload_receipt(
    bot,
    user_id: int,
    content: bytes,
    filename: str = "receipt.jpg",
    *,
    caption: str = "رسید پرداخت",
) -> str:
    """
    Send the photo to Telegram and return its ``file_id``.

    The photo is sent to the customer's own chat, so they can see that their
    receipt reached us.
    """
    url = f"{_API}/bot{settings.BOT_TOKEN}/sendPhoto"
    files = {"photo": (filename, content, "image/jpeg")}
    data = {"chat_id": str(user_id), "caption": caption}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, data=data, files=files)

    try:
        payload = response.json()
    except ValueError:
        logger.error("Telegram returned non-JSON while uploading a receipt")
        raise RuntimeError("Telegram returned an unexpected response")

    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram rejected the file")

    photos = (payload.get("result") or {}).get("photo") or []
    if not photos:
        raise RuntimeError("Telegram did not return a photo")
    return photos[-1]["file_id"]
