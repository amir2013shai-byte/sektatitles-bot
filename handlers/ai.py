"""Команды генератора титулов и промо-кампаний. Доступ — только владельцу."""
from __future__ import annotations

import html
import os
import random
import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import aigen
import services as svc
from config import config
from catalog import RARITIES
from ui import title_html

router = Router(name="ai-generator")

BAD_WORDS = (
    "хуй", "пизд", "еба", "ебл", "бля", "сука", "мудак", "гандон",
    "fuck", "shit", "bitch", "asshole", "nigger",
)


def _owner(message: Message) -> bool:
    return config.is_owner_user(message.from_user)


def _safe_name(text: str) -> tuple[bool, str]:
    value = (text or "").strip()
    if len(value) < 2:
        return False, "Описание слишком короткое."
    if len(value) > 160:
        return False, "Описание длиннее 160 символов."
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        return False, "Описание содержит непечатаемые символы."
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold())
    if any(re.search(rf"(^|\s){re.escape(word)}(\s|$)", normalized) for word in BAD_WORDS):
        return False, "Название отклонено фильтром неприемлемых выражений."
    return True, "ok"


@router.message(Command("aititle", "titlegen"))
async def cmd_aititle(message: Message, command: CommandObject) -> None:
    if not _owner(message):
        await message.answer("⛔️ Генератор каталога доступен только владельцу бота.")
        return
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "<b>🤖 Генератор титулов</b>\n\n"
            "Формат: <code>/aititle описание [редкость]</code>\n"
            "Пример: <code>/aititle неоновый дракон legendary</code>\n\n"
            "Без OPENAI_API_KEY работает локальный генератор; с ключом может запросить "
            "короткое название у OpenAI. Название проходит фильтр перед добавлением."
        )
        return

    parts = args.rsplit(maxsplit=1)
    rarity = parts[1].lower() if len(parts) == 2 and parts[1].lower() in RARITIES else None
    description = parts[0] if rarity else args
    ok, reason = _safe_name(description)
    if not ok:
        await message.answer(f"❌ {reason}")
        return

    spec = aigen.generate(description, rarity)
    ai_name = await aigen.ai_name(description) if os.getenv("OPENAI_API_KEY") else None
    if ai_name:
        valid, _ = _safe_name(ai_name)
        if valid:
            spec["name"] = ai_name[:24]

    base_code = aigen._slug(spec["name"])
    code = base_code
    suffix = 2
    while await svc.get_title_by_code(code):
        code = f"{base_code[:19]}_{suffix}"
        suffix += 1
    spec["code"] = code
    try:
        title_id = await svc.add_title(
            code, spec["name"], spec["rarity"], spec["emoji"],
            spec["price"], spec["stars"], spec["stock"],
        )
    except Exception:
        await message.answer("Не удалось добавить титул — проверьте каталог и повторите.")
        return

    title = await svc.get_title(title_id)
    mode = "внешняя модель" if ai_name else "локальный генератор"
    await message.answer(
        f"🤖 <b>Титул создан ({mode})</b>\n\n"
        f"{title_html(title)}\n"
        f"Редкость: <b>{RARITIES[spec['rarity']]['label']}</b>\n"
        f"Код: <code>{code}</code>\n"
        f"Цена: {spec['price']} 🪙"
        + (f" · {spec['stars']} ⭐" if spec["stars"] else "")
        + "\n\nПроверить каталог: /titles"
    )


@router.message(Command("aipromo", "promogen"))
async def cmd_aipromo(message: Message, command: CommandObject) -> None:
    if not _owner(message):
        await message.answer("⛔️ Создавать промокоды может только владелец бота.")
        return
    args = (command.args or "").split()
    if not args:
        await message.answer(
            "<b>🎁 Генератор промокампании</b>\n\n"
            "Формат: <code>/aipromo ТЕМА [КОЛИЧЕСТВО] [МОНЕТЫ]</code>\n"
            "Пример: <code>/aipromo победа 5 1000</code>\n"
            "Создаёт уникальные одноразовые коды с монетной наградой."
        )
        return
    numeric = []
    while args and args[-1].isdigit() and len(numeric) < 2:
        numeric.insert(0, int(args.pop()))
    theme = " ".join(args) or "SEKTA"
    count = max(1, min(numeric[0], 20)) if numeric else 5
    coins = max(1, min(numeric[1], 100_000)) if len(numeric) > 1 else 500
    codes = aigen.campaign(theme, count=count, coins=coins)
    created = []
    for item in codes:
        code = item["code"]
        while await svc.get_promo(code):
            code = f"{code[:24]}{random.randint(10, 99)}"
        await svc.create_promo(
            code, kind="coins", coins=coins, uses_left=-1, per_user=1,
            created_by=message.from_user.id,
        )
        created.append(code)
    await message.answer(
        f"🎁 <b>Кампании «{html.escape(theme)}» созданы</b>\n\n"
        f"Награда: <b>{coins}</b> 🪙 · кодов: <b>{len(created)}</b> · "
        "каждый можно активировать один раз на пользователя:\n\n"
        + "\n".join(f"<code>{code}</code>" for code in created)
        + "\n\nКоманды промокодов остаются доступны только владельцу."
    )


@router.message(Command("aiemoji", "emojigen"))
async def cmd_aiemoji(message: Message, command: CommandObject) -> None:
    if not _owner(message):
        await message.answer("⛔️ Добавлять наборы эмодзи может только владелец бота.")
        return
    description = (command.args or "").strip()
    if not description:
        await message.answer(
            "<b>🎨 Тематический набор эмодзи</b>\n\n"
            "Формат: <code>/aiemoji дракон огонь космос</code>\n"
            "Набор добавится в /style для всех пользователей."
        )
        return
    valid, reason = _safe_name(description)
    if not valid:
        await message.answer(f"❌ {reason}")
        return
    emojis = aigen.emoji_pack(description)
    added = await svc.add_plain_emoji_set("ai:" + aigen._slug(description), emojis)
    await message.answer(
        f"🎨 <b>Набор подготовлен</b>\n\n"
        f"Тема: {html.escape(description)}\nЭмодзи: {' '.join(emojis)}\n"
        f"Новых в общем списке: <b>{added}</b>\n"
        "Пользователи найдут их в /style."
    )
