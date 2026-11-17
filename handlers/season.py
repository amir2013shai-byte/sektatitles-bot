"""Сезоны и витрина коллекции (Telegram WebApp)."""
from __future__ import annotations

import base64
import json
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

import activity
import services as svc
import ui
from config import config
from catalog import RARITIES

router = Router(name="season")




def showcase_url(payload: dict) -> str | None:
    """Готовая ссылка на витрину с данными в #hash (без бэкенда)."""
    base = (config.webapp_url or "").rstrip("/")
    if not base:
        return None
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{base}/#showcase={token}"


async def build_payload(user_id: int) -> dict:
    user = await svc.get_user(user_id)
    titles = await svc.inventory(user_id)
    equipped = await svc.get_title(user["equipped_id"]) if user and user["equipped_id"] else None
    style = await svc.get_emoji(user["emoji_id"]) if user and user["emoji_id"] else None
    return {
        "name": (user["first_name"] if user else "") or "",
        "uname": (user["username"] if user else "") or "",
        "coins": user["coins"] if user else 0,
        "points": await activity.my_season_points(user_id),
        "equipped": {
            "name": equipped["name"], "emoji": equipped["emoji"], "rarity": equipped["rarity"],
        } if equipped else None,
        "style": style["emoji"] if style else None,
        "titles": [
            {"n": t["name"], "e": t["emoji"], "r": t["rarity"]} for t in titles
        ],
    }


@router.message(Command("showcase", "витрина"))
async def cmd_showcase(message: Message) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    url = showcase_url(await build_payload(message.from_user.id))
    if url is None:
        await message.answer(
            "<b>🖼 Витрина коллекции</b>\n\n"
            "Витрина ещё не настроена: владельцу нужно задать <code>WEBAPP_URL</code> "
            "(адрес GitHub Pages репозитория).\n\n"
            "Пока доступен текстовый профиль: /profile"
        )
        return
    await message.answer(
        "<b>🖼 Ваша витрина</b>\n\nКрасивые карточки коллекции — откройте мини-приложение:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎴 Открыть витрину", web_app=WebAppInfo(url=url))
        ]]),
    )


@router.callback_query(F.data == "nav:showcase")
async def cb_showcase(callback: CallbackQuery) -> None:
    url = showcase_url(await build_payload(callback.from_user.id))
    if url is None:
        await callback.answer("Витрина не настроена (WEBAPP_URL)", show_alert=True)
        return
    await callback.message.answer(
        "🎴 Открыть витрину:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎴 Витрина коллекции", web_app=WebAppInfo(url=url))
        ]]),
    )
    await callback.answer()


# -------------------------------------------------------------------- сезон
@router.message(Command("season", "сезон"))
async def cmd_season(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if parts and parts[0].lower() in ("start", "старт"):
        if not config.is_owner_user(message.from_user):
            await message.answer("⛔️ Запускать сезон может только владелец бота.")
            return
        name_parts = parts[1:]
        days = 30
        if name_parts and name_parts[-1].isdigit():
            days = max(1, min(int(name_parts.pop()), 365))
        name = " ".join(name_parts) or "Сезон"
        season_id = await activity.start_season(name, days)
        await message.answer(
            f"🏆 Сезон «{html.escape(name)}» запущен (id {season_id}) на {days} дней.\n"
            "Очки: сообщение +1 (до 40/день), победа в дуэли +5, квест +10, аукцион +15."
        )
        return
    if parts and parts[0].lower() in ("end", "финиш", "стоп"):
        if not config.is_owner_user(message.from_user):
            await message.answer("⛔️ Закрывать сезон может только владелец бота.")
            return
        season_id, winners = await activity.close_season()
        if not winners:
            await message.answer("Сезон закрыт, но победителей нет.")
            return
        lines = ["🏁 <b>Сезон завершён!</b>\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, w in enumerate(winners):
            lines.append(
                f"{medals[i]} {html.escape(w['name'] or w['username'] or str(w['user_id']))} — "
                f"{w['points']} очков · +{w['coins']} 🪙"
                + (f" · титул «{w['title']}»" if w["title"] else "")
            )
        await message.answer("\n".join(lines))
        return
    await message.answer(await activity.season_leaderboard_text())


@router.callback_query(F.data == "nav:season")
async def cb_season(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        await activity.season_leaderboard_text(), reply_markup=ui.back_menu())
    await callback.answer()
