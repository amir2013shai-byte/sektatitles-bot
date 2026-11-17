"""
Смоук-тест Telegram-слоя без реального API: подменяем сессию бота фейковой
и прогоняем реальные апдейты (сообщения, нажатия кнопок, FSM-визарды).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# владелец бота для тестов (config читает env при импорте)
os.environ["OWNER_IDS"] = "900001"
os.environ["OWNER_USERNAMES"] = "owner"

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, Update, User

import db
from handlers import admin, common, shop, style
from main import build_dispatcher

OWNER = 900001
CHAT = -1001


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def make_request(self, bot, method: TelegramMethod[TelegramType], timeout: int | None = None):
        self.calls.append(method)
        name = type(method).__name__
        if name == "SendMessage":
            return self._message(method.chat_id, getattr(method, "text", ""))
        if name in ("EditMessageText", "SendMessage"):
            return self._message(method.chat_id, getattr(method, "text", ""))
        if name == "GetChatMember":
            from aiogram.types import ChatMemberAdministrator, ChatMemberMember

            if getattr(method, "user_id", OWNER) != OWNER:
                return ChatMemberMember.model_validate({
                    "status": "member",
                    "user": {"id": method.user_id, "is_bot": False, "first_name": "Stranger"},
                })

            return ChatMemberAdministrator.model_validate({
                "status": "administrator",
                "user": {"id": getattr(method, "user_id", OWNER), "is_bot": False, "first_name": "Owner"},
                "can_be_edited": False, "is_anonymous": False, "can_manage_chat": True,
                "can_delete_messages": True, "can_manage_video_chats": True,
                "can_restrict_members": True, "can_promote_members": False,
                "can_change_info": True, "can_invite_users": True,
                "can_post_stories": False, "can_edit_stories": False,
                "can_delete_stories": False, "can_send_welcome_messages": False,
            })
        if name == "GetChatAdministrators":
            return [
                type("M", (), {"user": User(id=OWNER, is_bot=False, first_name="Owner"), "status": "administrator",
                               "can_be_edited": False})(),
            ]
        if name == "GetStickerSet":
            sticker = type("S", (), {"emoji": "😀", "custom_emoji_id": "111"})()
            return type("S", (), {"name": method.name, "stickers": [sticker, sticker]})()
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def close(self) -> None:
        return None

    @staticmethod
    def _message(chat_id: int, text: str) -> Message:
        return Message(
            message_id=abs(hash(text)) % 10_000,
            date=datetime.now(timezone.utc),
            chat=Chat(id=int(chat_id), type="supergroup" if int(chat_id) < 0 else "private"),
            text=text,
            from_user=User(id=1, is_bot=True, first_name="Bot"),
        )

    def of(self, name: str) -> list[Any]:
        return [c for c in self.calls if type(c).__name__ == name]


def msg_update(text: str, user_id: int = OWNER, chat_id: int = CHAT, username: str = "owner") -> Update:
    return Update.model_validate({
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Owner", "username": username},
            "text": text,
        },
    })


def cb_update(data: str, user_id: int = OWNER, chat_id: int = CHAT) -> Update:
    return Update.model_validate({
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": user_id, "is_bot": False, "first_name": "Owner", "username": "owner"},
            "chat_instance": "1",
            "data": data,
            "message": {
                "message_id": 11,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"},
                "from": {"id": 1, "is_bot": True, "first_name": "Bot"},
                "text": "menu",
            },
        },
    })


async def main() -> None:
    errors: list[str] = []

    class Catcher(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                errors.append(record.getMessage() or repr(record.exc_info))

    logging.getLogger("titlebot").addHandler(Catcher())
    logging.basicConfig(level=logging.CRITICAL)

    tmp = tempfile.mkdtemp()
    await db.init_db(os.path.join(tmp, "smoke.sqlite3"))

    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = build_dispatcher()

    async def feed(update: Update) -> None:
        # обходим middleware от aiogram, работающий только с реальным bot context
        await dp.feed_update(bot, update)

    steps = [
        ("/start", msg_update("/start")),
        ("меню магазина", cb_update("nav:shop")),
        ("карточка титула", cb_update("t:1")),
        ("покупка за монеты", cb_update("buy:1")),
        ("профиль", cb_update("nav:profile")),
        ("эмодзи", cb_update("nav:style")),
        ("выбор эмодзи", cb_update("em:3")),
        ("топ", cb_update("nav:top")),
        ("бонус дня", cb_update("nav:daily")),
        ("промокод (нет такого)", msg_update("NOSUCHCODE")),
        ("админка", cb_update("nav:admin")),
        ("массовая выдача шаг 1", cb_update("adm:mass")),
        ("массовая выдача шаг 2", cb_update("mt:2")),
        ("массовая выдача шаг 3", cb_update("ma:2:chat")),
        ("массовая выдача подтверждение", cb_update("mc:2:chat")),
        ("/addtitle", msg_update("/addtitle")),
        ("данные титула", msg_update("cyber | Кибер-ниндзя | epic | 🥷 | 5000 | 0 | -1")),
        ("/grant", msg_update("/grant novice")),
        ("/newpromo", msg_update("/newpromo")),
        ("код промокода", msg_update("DRAGON2026")),
        ("тип промокода", msg_update("3")),
        ("значение промокода", msg_update("dragon 500")),
        ("активаций", msg_update("100")),
        ("активация промокода", msg_update("DRAGON2026")),
        ("/stats", msg_update("/stats")),
        ("/sync", msg_update("/sync")),
        ("/loademoji", msg_update("/loademoji TestSet")),
        ("/titles", msg_update("/titles")),
        ("/help", msg_update("/help")),
        ("/profile", msg_update("/profile")),
        ("/top", msg_update("/top")),
        ("/daily", msg_update("/daily")),
        ("/backup (не владелец)", msg_update("/backup", user_id=555)),
        ("/quests", msg_update("/quests")),
        ("меню квестов", cb_update("nav:quests")),
        ("/season", msg_update("/season")),
        ("/dice", msg_update("/dice 100")),
        ("/craft", msg_update("/craft")),
        ("/lootbox", msg_update("/lootbox")),
        ("/auction", msg_update("/auction")),
        ("/rentals", msg_update("/rentals")),
        ("аренда из меню", cb_update("nav:rentals")),
        ("/showcase", msg_update("/showcase")),
        ("/aititle (владелец)", msg_update("/aititle smoke dragon")),
        ("/aiemoji (владелец)", msg_update("/aiemoji космос дракон")),
        ("/aipromo (владелец)", msg_update("/aipromo smoke 2 100")),
        ("/aititle (не владелец)", msg_update("/aititle secret", user_id=777, username="stranger")),
        ("/aipromo (не владелец)", msg_update("/aipromo secret", user_id=777, username="stranger")),
    ]

    for label, update in steps:
        before = len(session.calls)
        await feed(update)
        produced = session.calls[before:]
        print(f"  ✓ {label}: {len(produced)} API-вызов(ов)")
        if not produced:
            errors.append(f"нет ответа на шаг: {label}")

    texts = [getattr(c, "text", "") for c in session.calls if getattr(c, "text", None)]
    assert any("Магазин" in t for t in texts), "нет текста магазина"
    assert any("Промокод активирован" in t for t in texts), "промокод не сработал"
    assert any("Промокод создан" in t for t in texts), "мастер создания промокода сломан"
    assert any("Готово" in t for t in texts), "массовая выдача не завершилась"
    assert any("Титул добавлен" in t for t in texts), "добавление титула сломалось"
    assert any("Задания" in t for t in texts), "/quests не ответил"
    assert any("Сезон" in t for t in texts), "/season не ответил"
    assert any("Титул создан" in t for t in texts), "/aititle не создал титул"
    assert any("Кампании" in t for t in texts), "/aipromo не создал промокампанию"
    assert any("Набор подготовлен" in t for t in texts), "/aiemoji не добавил набор"
    assert any("только владельцу" in t or "только владельца" in t for t in texts),         "ИИ-команды должны быть закрыты для посторонних"

    # Middleware должен считать обычное сообщение группы, но не команды/личные сообщения.
    from quests import get_counter
    before = await get_counter(777777, "messages")
    await feed(msg_update("а", user_id=777777, username="walker"))
    after = await get_counter(777777, "messages")
    assert after == before + 1, "групповое обычное сообщение не попало в счётчик квеста"
    assert await get_counter(777777, "letter_messages:а") == 1, "секретный счётчик разных сообщений не обновился"
    from handlers.quests_h import quests_text
    public_quests = await quests_text(OWNER)
    assert "Буквоед" not in public_quests and "Эгоист" not in public_quests,         "секретные задания не должны отображаться в /quests"
    print("  ✓ тексты ответов корректны")

    # только владелец может создавать промокоды: обычный админ чата получает отказ
    before = len(session.calls)
    await feed(msg_update("/newpromo", user_id=777, username="someadmin"))
    denied_promo = session.calls[before:]
    assert denied_promo and "только владельцу" in getattr(denied_promo[-1], "text", "").lower(), \
        "админ чата не должен создавать промокоды"
    print("  ✓ промокоды доступны только владельцу")

    # /whoami показывает свои права
    before = len(session.calls)
    await feed(msg_update("/whoami"))
    whoami = session.calls[before:]
    assert whoami and "Владелец бота" in getattr(whoami[-1], "text", "")
    print("  ✓ /whoami отвечает")

    # проверка отказа в правах: обычный юзер в чужом чате не может массово выдавать
    before = len(session.calls)
    await feed(msg_update("/mass", user_id=555, username="stranger"))
    denied = session.calls[before:]
    assert denied and any("администраторам" in (getattr(c, "text", "") or "").lower() for c in denied), \
        "нет защиты от не-админа"
    print("  ✓ не-админ получает отказ")

    assert not errors, f"ошибки в хендлерах: {errors[:3]}"
    await db.close_db()
    await bot.session.close()
    print("\nСмоук-тест Telegram-слоя пройден.")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    os._exit(0)  # aiosqlite держит фоновый поток — выходим жёстко
