"""
Резервные копии БД в Telegram.
Бесплатные хостинги (Render/Railway) стирают диск при перезапуске — поэтому
владелец может в любой момент скачать базу себе в чат и восстановить её.

/backup          — прислать файл базы владельцу
/restore         — ответом на файл .sqlite3 восстановить базу
AUTO_BACKUP_HOURS — авто-бэкап владельцу раз в N часов (0 = выключено)
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

import db
from config import config

router = Router(name="backup")
log = logging.getLogger(__name__)


def _is_owner(user_id: int) -> bool:
    return config.is_owner(user_id)


async def send_backup(bot: Bot, chat_id: int, note: str = "") -> None:
    tmp = os.path.join(tempfile.gettempdir(), "titlebot-backup.sqlite3")
    path = await db.backup_to(tmp)
    size_kb = round(os.path.getsize(path) / 1024, 1)
    await bot.send_document(
        chat_id=chat_id,
        document=FSInputFile(path, filename="titlebot.sqlite3"),
        caption=f"💾 Бэкап базы ({size_kb} КБ). {note}\nВосстановить: ответьте на файл командой /restore",
    )


@router.message(Command("backup"))
async def cmd_backup(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        await message.answer("Команда только для владельца бота (OWNER_IDS).")
        return
    await send_backup(message.bot, message.from_user.id)  # type: ignore[arg-type]
    if message.chat.type != "private":
        await message.answer("💾 Бэкап отправлен в личку.")


@router.message(Command("restore"))
async def cmd_restore(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        await message.answer("Команда только для владельца бота (OWNER_IDS).")
        return
    doc = message.reply_to_message.document if message.reply_to_message else None
    if doc is None:
        await message.answer("Ответьте командой <code>/restore</code> на файл базы (titlebot.sqlite3).")
        return
    tmp = os.path.join(tempfile.gettempdir(), f"restore-{doc.file_unique_id}.sqlite3")
    await message.bot.download(doc, destination=tmp)  # type: ignore[union-attr]

    # проверяем, что это действительно наша база
    import sqlite3

    try:
        check = sqlite3.connect(tmp)
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check.close()
    except Exception as exc:
        await message.answer(f"❌ Файл не похож на базу SQLite: {exc}")
        return
    if not {"users", "titles", "inventory"} <= tables:
        await message.answer("❌ В файле нет нужных таблиц — это не база этого бота.")
        return

    await db.restore_from(tmp)
    await message.answer(
        "✅ База восстановлена.\n"
        f"Пользователей: {(await db.scalar('SELECT COUNT(*) FROM users'))}, "
        f"титулов выдано: {(await db.scalar('SELECT COUNT(*) FROM inventory'))}."
    )


async def auto_backup_loop(bot: Bot, interval_hours: float) -> None:
    """Фоновая задача: бэкап владельцу каждые N часов."""
    if interval_hours <= 0 or not config.owner_ids:
        return
    await asyncio.sleep(60)
    while True:
        try:
            for owner in config.owner_ids:
                await send_backup(bot, owner, note="(авто)")
        except Exception as exc:
            log.warning("auto backup failed: %s", exc)
        await asyncio.sleep(interval_hours * 3600)
