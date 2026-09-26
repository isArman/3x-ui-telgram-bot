"""Admin broadcast: send one message to every bot user.

Rate-limited to stay well under Telegram's per-chat limits, and tolerant of
per-recipient failures (blocked bot, deactivated account) so one bad recipient
never aborts the run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.utils.logger import logger

# Telegram allows ~30 messages/second overall; stay comfortably below it.
_SEND_INTERVAL_SECONDS = 0.05


@dataclass
class BroadcastResult:
    total: int = 0
    sent: int = 0
    failed: int = 0
    failed_ids: list[int] = field(default_factory=list)

    @property
    def summary(self) -> str:
        lines = [f"ارسال شد: {self.sent} از {self.total}"]
        if self.failed:
            lines.append(f"ناموفق: {self.failed}")
        return "\n".join(lines)


async def list_recipient_ids(session: AsyncSession) -> list[int]:
    """Every known user id, newest first (stable, no blocked filter)."""
    result = await session.execute(select(User.id).order_by(User.id.desc()))
    return [row[0] for row in result.all()]


async def broadcast_message(
    bot: Bot,
    session: AsyncSession,
    text: str,
    *,
    recipient_ids: list[int] | None = None,
) -> BroadcastResult:
    """Send `text` to every user; returns a per-run summary."""
    result = BroadcastResult()
    ids = recipient_ids if recipient_ids is not None else await list_recipient_ids(session)
    result.total = len(ids)

    for user_id in ids:
        try:
            await bot.send_message(chat_id=user_id, text=text)
            result.sent += 1
        except TelegramRetryAfter as exc:
            # Telegram asked us to slow down; wait and retry this one once.
            logger.warning("Broadcast rate-limited, sleeping %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.send_message(chat_id=user_id, text=text)
                result.sent += 1
            except Exception as retry_exc:
                result.failed += 1
                result.failed_ids.append(user_id)
                logger.warning("Broadcast retry failed for %s: %s", user_id, retry_exc)
        except TelegramForbiddenError:
            # User blocked the bot or deleted the account.
            result.failed += 1
            result.failed_ids.append(user_id)
        except Exception as exc:
            result.failed += 1
            result.failed_ids.append(user_id)
            logger.warning("Broadcast failed for %s: %s", user_id, exc)

        await asyncio.sleep(_SEND_INTERVAL_SECONDS)

    logger.info(
        "Broadcast finished: sent=%s failed=%s total=%s",
        result.sent,
        result.failed,
        result.total,
    )
    return result
