"""Тексты и клавиатуры."""
from __future__ import annotations

import html
from typing import Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from catalog import RARITIES

SHOP_PAGE = 5
EMOJI_PAGE = 24


def esc(text: str | None) -> str:
    return html.escape(text or "")


def emoji_html(emoji: str, custom_emoji_id: str | None = None) -> str:
    """Обычный эмодзи или премиум-эмодзи (Telegram Premium), с фолбэком."""
    if custom_emoji_id:
        return f'<tg-emoji emoji-id="{custom_emoji_id}">{esc(emoji)}</tg-emoji>'
    return esc(emoji)


def title_html(title, override: Sequence[str] | None = None) -> str:
    """Название титула с эмодзи. override = (emoji, custom_emoji_id)."""
    if override:
        emoji, cid = override
    else:
        emoji, cid = title["emoji"], title["custom_emoji_id"]
    return f"{emoji_html(emoji, cid)} <b>{esc(title['name'])}</b>"


def rarity_line(rarity: str) -> str:
    info = RARITIES.get(rarity, RARITIES["common"])
    return f"{info['dot']} {info['label']} {info['stars']}".strip()


def price_line(title) -> str:
    parts = []
    if title["price"]:
        parts.append(f"{title['price']} 🪙")
    if title["stars"]:
        parts.append(f"{title['stars']} ⭐")
    if not parts:
        parts.append("только за награды")
    if title["stock"] > 0:
        parts.append(f"осталось {title['stock']} шт.")
    return " · ".join(parts)


def user_link(row) -> str:
    name = esc(row["first_name"] or row["username"] or str(row["user_id"]))
    return f'<a href="tg://user?id={row["user_id"]}">{name}</a>'


# -------------------------------------------------------------------- меню
def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎭 Профиль", callback_data="nav:profile"),
         InlineKeyboardButton(text="🛒 Магазин", callback_data="nav:shop")],
        [InlineKeyboardButton(text="🎨 Эмодзи", callback_data="nav:style"),
         InlineKeyboardButton(text="🎁 Промокод", callback_data="nav:promo")],
        [InlineKeyboardButton(text="🏆 Топ", callback_data="nav:top"),
         InlineKeyboardButton(text="💫 Бонус дня", callback_data="nav:daily")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="nav:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="nav:menu")]
    ])


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Массовая выдача", callback_data="adm:mass")],
        [InlineKeyboardButton(text="🎯 Выдать титул", callback_data="adm:grant"),
         InlineKeyboardButton(text="🧾 Промокоды", callback_data="adm:promos")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
         InlineKeyboardButton(text="📚 Титулы", callback_data="adm:titles")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="nav:menu")],
    ])


# ------------------------------------------------------------------- магазин
def shop_keyboard(titles: Sequence, page: int, owned: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    chunk = titles[page * SHOP_PAGE:(page + 1) * SHOP_PAGE]
    for t in chunk:
        mark = "✅ " if t["id"] in owned else ""
        price = f"{t['price']} 🪙" if t["price"] else (f"{t['stars']} ⭐" if t["stars"] else "🎁")
        rows.append([InlineKeyboardButton(
            text=f"{mark}{t['emoji']} {t['name']} · {price}", callback_data=f"t:{t['id']}"
        )])
    pages = max(1, (len(titles) + SHOP_PAGE - 1) // SHOP_PAGE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"shop:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"shop:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def title_card_keyboard(title, is_owned: bool, is_equipped: bool, payments_on: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if is_owned:
        if is_equipped:
            rows.append([InlineKeyboardButton(text="✅ Надет — снять", callback_data=f"un:t:{title['id']}")])
        else:
            rows.append([InlineKeyboardButton(text="🎭 Надеть", callback_data=f"eq:{title['id']}")])
    else:
        if title["price"] and title["stock"] != 0:
            rows.append([InlineKeyboardButton(
                text=f"Купить за {title['price']} 🪙", callback_data=f"buy:{title['id']}"
            )])
        if title["stars"] and payments_on and title["stock"] != 0:
            rows.append([InlineKeyboardButton(
                text=f"Купить за {title['stars']} ⭐", callback_data=f"star:{title['id']}"
            )])
    rows.append([InlineKeyboardButton(text="⬅️ К магазину", callback_data="nav:shop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# -------------------------------------------------------------------- эмодзи
def emoji_keyboard(emojis: Sequence, page: int, current_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    chunk = emojis[page * EMOJI_PAGE:(page + 1) * EMOJI_PAGE]
    line: list[InlineKeyboardButton] = []
    for e in chunk:
        mark = "·" if e["id"] == current_id else ""
        line.append(InlineKeyboardButton(text=f"{mark}{e['emoji']}", callback_data=f"em:{e['id']}"))
        if len(line) == 6:
            rows.append(line)
            line = []
    if line:
        rows.append(line)

    pages = max(1, (len(emojis) + EMOJI_PAGE - 1) // EMOJI_PAGE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"style:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"style:{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="♻️ Сбросить", callback_data="em:0")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------------------------------------------- массовая выдача (FSM)
def mass_titles_keyboard(titles: Sequence, page: int = 0) -> InlineKeyboardMarkup:
    per_page = 8
    rows = []
    chunk = titles[page * per_page:(page + 1) * per_page]
    for t in chunk:
        rows.append([InlineKeyboardButton(
            text=f"{t['emoji']} {t['name']}", callback_data=f"mt:{t['id']}"
        )])
    pages = max(1, (len(titles) + per_page - 1) // per_page)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"mtp:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"mtp:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_audience_keyboard(title_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Всех в этом чате", callback_data=f"ma:{title_id}:chat")],
        [InlineKeyboardButton(text="👑 Админов чата", callback_data=f"ma:{title_id}:admins")],
        [InlineKeyboardButton(text="🌍 Всех пользователей бота", callback_data=f"ma:{title_id}:users")],
        [InlineKeyboardButton(text="🛍 Покупателей титулов", callback_data=f"ma:{title_id}:buyers")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:cancel")],
    ])


def mass_confirm_keyboard(title_id: int, mode: str, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ Выдать {count} чел.", callback_data=f"mc:{title_id}:{mode}"
        )],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:cancel")],
    ])


def promo_confirm_none() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В админку", callback_data="nav:admin")]
    ])
