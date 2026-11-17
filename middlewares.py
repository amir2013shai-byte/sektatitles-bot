"""Middleware: авторегистрация, активность чата и автоматические награды."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

import activity
import quests
import services as svc

log = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


class UserTracking(BaseMiddleware):
    """Создаёт пользователя, считает групповые сообщения и обрабатывает квесты."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        chat_id = getattr(chat, "id", None)
        try:
            if user and not user.is_bot:
                await svc.ensure_user(user.id, user.username, user.first_name)
                if chat_id:
                    await svc.touch_chat(chat_id, user.id)
        except Exception as exc:
            log.warning("user tracking failed: %s", exc)

        result = await handler(event, data)

        # Засчитываем только обычные текстовые сообщения в группах: команды, личка,
        # подписи к медиа и сообщения ботов не дают фермерский прогресс.
        try:
            msg = event.message if isinstance(event, Update) else getattr(event, "message", None)
            if (
                user and not user.is_bot and msg is not None and msg.text
                and msg.chat.type in ("group", "supergroup")
                and not msg.text.lstrip().startswith("/")
            ):
                await svc.ensure_user(user.id, user.username, user.first_name)
                today_count = await activity.bump_activity(msg.chat.id, user.id)
                if today_count <= activity.DAILY_MESSAGE_CAP:
                    await activity.add_season_points(
                        user.id, activity.SEASON_POINTS["message"])

                night = datetime.now(MOSCOW).hour in range(0, 5)
                completed = await quests.record_message(user.id, msg.text, night=night)
                for title in await activity.check_auto_titles(user.id):
                    await msg.answer(
                        f"🏅 <b>Новая награда за активность!</b>\n\n"
                        f"Вам выдан титул {title['emoji']} <b>{title['name']}</b>"
                    )
                for quest in completed:
                    await msg.answer(
                        f"❓ <b>Секретное задание выполнено!</b>\n\n"
                        f"Вы сделали то, о чём никто не знал: <b>{quest.name}</b>\n"
                        "Награда уже начислена — проверьте /profile"
                    )
        except Exception as exc:
            log.warning("activity tracking failed: %s", exc)

        return result
