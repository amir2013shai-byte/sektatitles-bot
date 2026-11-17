"""Middleware: авторегистрация пользователей и учёт участников чата."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

import services as svc

log = logging.getLogger(__name__)


class UserTracking(BaseMiddleware):
    """Создаёт запись пользователя и запоминает его в чате (для массовой выдачи)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            user = data.get("event_from_user")
            if user and not user.is_bot:
                await svc.ensure_user(user.id, user.username, user.first_name)
                chat = data.get("event_chat")
                chat_id = getattr(chat, "id", None)
                if chat_id:
                    await svc.touch_chat(chat_id, user.id)
        except Exception as exc:  # трекинг не должен ломать апдейт
            log.warning("tracking failed: %s", exc)
        return await handler(event, data)
