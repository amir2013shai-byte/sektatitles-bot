"""Кастомизация эмодзи у титула (включая премиум-эмодзи из наборов Telegram)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import services as svc
import ui

router = Router(name="style")


async def style_text(user_id: int) -> str:
    user = await svc.get_user(user_id)
    style = await svc.get_emoji(user["emoji_id"]) if user and user["emoji_id"] else None
    equipped = await svc.get_title(user["equipped_id"]) if user and user["equipped_id"] else None

    current = f"{ui.emoji_html(style['emoji'], style['custom_emoji_id'])}" if style else "<i>по умолчанию</i>"
    preview = ui.title_html(equipped, (style["emoji"], style["custom_emoji_id"])) if (equipped and style) \
        else (ui.title_html(equipped) if equipped else "<i>титул не надет</i>")

    extra = ""
    if style and style["set_name"] and style["set_name"] != "base":
        extra = f"\nНабор: <b>{ui.esc(style['set_name'])}</b> (премиум-эмодзи)"

    return (
        "<b>🎨 Кастомизация эмодзи</b>\n\n"
        f"Текущий эмодзи: {current}{extra}\n"
        f"Превью титула: {preview}\n\n"
        "Выберите эмодзи — он применится к любому вашему титулу.\n"
        "<i>Премиум-эмодзи видны тем, у кого есть Telegram Premium; "
        "остальные увидят обычный эмодзи-фолбэк.</i>"
    )


async def render_style(callback: CallbackQuery, page: int = 0) -> None:
    emojis = await svc.emoji_list()
    user = await svc.get_user(callback.from_user.id)
    current = user["emoji_id"] if user else None
    await callback.message.edit_text(
        await style_text(callback.from_user.id),
        reply_markup=ui.emoji_keyboard(emojis, page, current),
    )


@router.message(Command("style", "emoji"))
async def cmd_style(message: Message) -> None:
    emojis = await svc.emoji_list()
    user = await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        await style_text(message.from_user.id),
        reply_markup=ui.emoji_keyboard(emojis, 0, user["emoji_id"]),
    )


@router.callback_query(F.data == "nav:style")
async def cb_style(callback: CallbackQuery) -> None:
    await render_style(callback, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("style:"))
async def cb_style_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    await render_style(callback, page)
    await callback.answer()


@router.callback_query(F.data.startswith("em:"))
async def cb_set_emoji(callback: CallbackQuery) -> None:
    emoji_id = int(callback.data.split(":")[1])
    if emoji_id == 0:
        await svc.set_emoji(callback.from_user.id, None)
        await callback.answer("Эмодзи сброшен")
    else:
        emoji = await svc.get_emoji(emoji_id)
        if emoji is None:
            await callback.answer("Эмодзи недоступен", show_alert=True)
            return
        await svc.set_emoji(callback.from_user.id, emoji_id)
        await callback.answer(f"Эмодзи {emoji['emoji']} выбран")

    emojis = await svc.emoji_list()
    user = await svc.get_user(callback.from_user.id)
    await callback.message.edit_text(
        await style_text(callback.from_user.id),
        reply_markup=ui.emoji_keyboard(emojis, 0, user["emoji_id"] if user else None),
    )
