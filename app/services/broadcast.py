"""Admin broadcast: send a message to every bot user.

Rate-limited to stay well under Telegram's per-chat limits, and tolerant of
per-recipient failures (blocked bot, deactivated account) so one bad recipient
never aborts the run.

Every broadcast is stored, together with where each message landed, so the
admin can preview it, resend it or edit the messages that were already sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Broadcast, BroadcastMessage, User
from app.utils.logger import logger

# Telegram allows ~30 messages/second overall; stay comfortably below it.
_SEND_INTERVAL_SECONDS = 0.05

BROADCAST_TARGET_ALL = "all"
BROADCAST_TARGET_USER = "user"


@dataclass
class BroadcastResult:
    total: int = 0
    sent: int = 0
    failed: int = 0
    failed_ids: list[int] = field(default_factory=list)
    broadcast_id: int | None = None
    edited: int = 0

    @property
    def summary(self) -> str:
        lines = [f"ارسال شد: {self.sent} از {self.total}"]
        if self.failed:
            lines.append(f"ناموفق: {self.failed}")
        if self.edited:
            lines.append(f"ویرایش شد: {self.edited}")
        return "\n".join(lines)


async def list_recipient_ids(session: AsyncSession) -> list[int]:
    """Every known user id, newest first (stable, no blocked filter)."""
    result = await session.execute(select(User.id).order_by(User.id.desc()))
    return [row[0] for row in result.all()]


async def create_broadcast(
    session: AsyncSession,
    text: str,
    *,
    use_html: bool = False,
    target: str = BROADCAST_TARGET_ALL,
    target_chat_id: int | None = None,
    created_by: int | None = None,
) -> Broadcast:
    """Store a broadcast as a draft so it can be previewed before sending."""
    broadcast = Broadcast(
        text=text,
        use_html=use_html,
        target=target,
        target_chat_id=target_chat_id,
        status="draft",
        created_by=created_by,
    )
    session.add(broadcast)
    await session.commit()
    await session.refresh(broadcast)
    return broadcast


async def list_broadcasts(session: AsyncSession, limit: int = 10) -> list[Broadcast]:
    result = await session.execute(
        select(Broadcast).order_by(Broadcast.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_broadcast(
    session: AsyncSession, broadcast_id: int
) -> Broadcast | None:
    result = await session.execute(
        select(Broadcast).where(Broadcast.id == broadcast_id)
    )
    return result.scalar_one_or_none()


async def last_broadcast(session: AsyncSession) -> Broadcast | None:
    result = await session.execute(
        select(Broadcast).order_by(Broadcast.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


def _parse_mode(use_html: bool) -> str | None:
    return "HTML" if use_html else None


async def _send_one(bot: Bot, chat_id: int, text: str, parse_mode: str | None):
    """Send one message, only passing parse_mode when it is actually needed."""
    if parse_mode:
        return await bot.send_message(
            chat_id=chat_id, text=text, parse_mode=parse_mode
        )
    return await bot.send_message(chat_id=chat_id, text=text)


def _message_id(sent) -> int:
    """The Telegram message id, or 0 when the response has none."""
    value = getattr(sent, "message_id", 0)
    return value if isinstance(value, int) else 0


async def broadcast_message(
    bot: Bot,
    session: AsyncSession,
    text: str,
    *,
    recipient_ids: list[int] | None = None,
) -> BroadcastResult:
    """Backwards-compatible helper: send a plain-text message to every user."""
    broadcast = await create_broadcast(session, text, use_html=False)
    return await send_broadcast(
        bot, session, broadcast, recipient_ids=recipient_ids
    )


async def send_broadcast(
    bot: Bot,
    session: AsyncSession,
    broadcast: Broadcast,
    *,
    recipient_ids: list[int] | None = None,
) -> BroadcastResult:
    """Send a stored broadcast and remember where each message landed."""
    result = BroadcastResult()
    result.broadcast_id = broadcast.id

    if recipient_ids is None:
        if broadcast.target == BROADCAST_TARGET_USER and broadcast.target_chat_id:
            recipient_ids = [broadcast.target_chat_id]
        else:
            recipient_ids = await list_recipient_ids(session)

    result.total = len(recipient_ids)
    parse_mode = _parse_mode(broadcast.use_html)

    for user_id in recipient_ids:
        try:
            sent = await _send_one(bot, user_id, broadcast.text, parse_mode)
            session.add(
                BroadcastMessage(
                    broadcast_id=broadcast.id,
                    chat_id=user_id,
                    message_id=_message_id(sent),
                )
            )
            result.sent += 1
        except TelegramRetryAfter as exc:
            # Telegram asked us to slow down; wait and retry this one once.
            logger.warning("Broadcast rate-limited, sleeping %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            try:
                sent = await _send_one(bot, user_id, broadcast.text, parse_mode)
                session.add(
                    BroadcastMessage(
                        broadcast_id=broadcast.id,
                        chat_id=user_id,
                        message_id=_message_id(sent),
                    )
                )
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

    broadcast.status = "sent"
    broadcast.sent_count = result.sent
    broadcast.failed_count = result.failed
    await session.commit()

    logger.info(
        "Broadcast finished: sent=%s failed=%s total=%s",
        result.sent,
        result.failed,
        result.total,
    )
    return result


async def edit_last_broadcast(
    bot: Bot,
    session: AsyncSession,
    text: str,
    *,
    use_html: bool = False,
) -> int:
    """
    Edit the messages of the most recent broadcast.

    Returns the number of messages that were edited.
    """
    broadcast = await last_broadcast(session)
    if not broadcast:
        return 0

    result = await session.execute(
        select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast.id)
    )
    rows = list(result.scalars().all())

    parse_mode = _parse_mode(use_html)
    edited = 0
    for row in rows:
        try:
            await bot.edit_message_text(
                chat_id=row.chat_id,
                message_id=row.message_id,
                text=text,
                parse_mode=parse_mode,
            )
            edited += 1
        except Exception as exc:
            logger.warning(
                "Could not edit broadcast message %s in chat %s: %s",
                row.message_id,
                row.chat_id,
                exc,
            )

    if edited:
        broadcast.text = text
        broadcast.use_html = use_html
        await session.commit()

    return edited
