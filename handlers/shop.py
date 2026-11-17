"""Магазин титулов, надевание, оплата звёздами Telegram."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import services as svc
import ui
from config import config
from handlers.common import can_manage

router = Router(name="shop")
log = logging.getLogger(__name__)


async def shop_text(page: int) -> str:
    return (
        "<b>🛒 Магазин титулов</b>\n\n"
        "Цена в 🪙 — внутриигровые монеты (бонус дня, промокоды, награды).\n"
        "Цена в ⭐ — звёзды Telegram, покупается сразу на аккаунт.\n\n"
        "Нажмите на титул, чтобы посмотреть карточку 👇"
    )


async def render_shop(message: Message, user_id: int, page: int = 0) -> None:
    titles = await svc.all_titles()
    owned = {t["id"] for t in await svc.inventory(user_id)}
    await message.edit_text(await shop_text(page), reply_markup=ui.shop_keyboard(titles, page, owned))


@router.message(Command("shop"))
async def cmd_shop(message: Message) -> None:
    titles = await svc.all_titles()
    owned = {t["id"] for t in await svc.inventory(message.from_user.id)}
    await message.answer(await shop_text(0), reply_markup=ui.shop_keyboard(titles, 0, owned))


@router.callback_query(F.data == "nav:shop")
async def cb_shop(callback: CallbackQuery) -> None:
    await render_shop(callback.message, callback.from_user.id, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    await render_shop(callback.message, callback.from_user.id, page)
    await callback.answer()


# ------------------------------------------------------------- карточка титула
async def title_card_text(title, user_id: int) -> str:
    owned = await svc.owns(user_id, title["id"])
    user = await svc.get_user(user_id)
    equipped = bool(user and user["equipped_id"] == title["id"])
    status = "✅ <b>Надет</b>" if equipped else ("📦 В коллекции" if owned else "🔒 Не куплен")

    return (
        f"{ui.title_html(title)}\n"
        f"{ui.rarity_line(title['rarity'])}\n\n"
        f"Код: <code>{ui.esc(title['code'])}</code>\n"
        f"Цена: <b>{ui.price_line(title)}</b>\n"
        f"Статус: {status}"
    )


@router.callback_query(F.data.startswith("t:"))
async def cb_title_card(callback: CallbackQuery) -> None:
    title_id = int(callback.data.split(":")[1])
    title = await svc.get_title(title_id)
    if title is None:
        await callback.answer("Титул не найден", show_alert=True)
        return
    user = await svc.get_user(callback.from_user.id)
    equipped = bool(user and user["equipped_id"] == title_id)
    owned = await svc.owns(callback.from_user.id, title_id)
    await callback.message.edit_text(
        await title_card_text(title, callback.from_user.id),
        reply_markup=ui.title_card_keyboard(title, owned, equipped, config.payments_enabled),
    )
    await callback.answer()


# ------------------------------------------------------------ надень / сними
async def apply_custom_admin_title(bot: Bot, chat_id: int, user_id: int, title) -> None:
    """Если пользователь — админ чата, дублируем титул в настоящий «кастомный титул» Telegram."""
    if not title:
        return
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if getattr(member, "can_be_edited", False) or member.status in ("administrator",):
            await bot.set_chat_administrator_custom_title(chat_id, user_id, title["name"][:16])
    except Exception as exc:  # не админ / нет прав у бота — просто пропускаем
        log.debug("custom title skipped: %s", exc)


@router.callback_query(F.data.startswith("eq:"))
async def cb_equip(callback: CallbackQuery) -> None:
    title_id = int(callback.data.split(":")[1])
    try:
        title = await svc.equip(callback.from_user.id, title_id)
    except PermissionError:
        await callback.answer("Сначала купите этот титул", show_alert=True)
        return
    if callback.message.chat.type in ("group", "supergroup") and title:
        await apply_custom_admin_title(callback.bot, callback.message.chat.id, callback.from_user.id, title)  # type: ignore[arg-type]
    await callback.message.edit_text(
        f"🎭 Титул надет: {ui.title_html(title)}\n\n" + await profile_snippet(callback.from_user.id),
        reply_markup=ui.back_menu(),
    )
    await callback.answer("Готово!")


@router.callback_query(F.data.startswith("un:t:"))
async def cb_unequip(callback: CallbackQuery) -> None:
    title_id = int(callback.data.split(":")[2])
    await svc.equip(callback.from_user.id, None)
    await callback.message.edit_text(
        "🎭 Титул снят.\n\n" + await profile_snippet(callback.from_user.id),
        reply_markup=ui.back_menu(),
    )
    await callback.answer()


async def profile_snippet(user_id: int) -> str:
    from handlers.common import profile_text

    return await profile_text(user_id)


# ------------------------------------------------------------------ покупка
@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_coins(callback: CallbackQuery) -> None:
    title_id = int(callback.data.split(":")[1])
    ok, reason = await svc.buy_with_coins(callback.from_user.id, title_id)
    title = await svc.get_title(title_id)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return
    if callback.message.chat.type in ("group", "supergroup") and title:
        await apply_custom_admin_title(callback.bot, callback.message.chat.id, callback.from_user.id, title)  # type: ignore[arg-type]
    text = (
        f"✅ Покупка успешна!\n\n{ui.title_html(title)} уже в вашей коллекции и надет.\n\n"
        + await profile_snippet(callback.from_user.id)
    )
    owned = await svc.owns(callback.from_user.id, title_id)
    await callback.message.edit_text(
        text, reply_markup=ui.title_card_keyboard(title, owned, True, config.payments_enabled)
    )
    await callback.answer("Куплено!")


# ------------------------------------------------------- оплата звёздами (XTR)
@router.callback_query(F.data.startswith("star:"))
async def cb_buy_stars(callback: CallbackQuery) -> None:
    if not config.payments_enabled:
        await callback.answer("Оплата звёздами отключена", show_alert=True)
        return
    title_id = int(callback.data.split(":")[1])
    title = await svc.get_title(title_id)
    if title is None or not title["stars"]:
        await callback.answer("Этот титул нельзя купить за звёзды", show_alert=True)
        return
    if await svc.owns(callback.from_user.id, title_id):
        await callback.answer("У вас уже есть этот титул", show_alert=True)
        return

    await callback.bot.send_invoice(  # type: ignore[union-attr]
        chat_id=callback.from_user.id,
        title=f"Титул «{title['name']}»",
        description=f"Титул {title['rarity']} редкости навсегда. Оплата звёздами Telegram.",
        payload=f"title:{title_id}",
        provider_token=config.stars_provider_token,
        currency="XTR",
        prices=[LabeledPrice(label=f"Титул «{title['name']}»", amount=int(title["stars"]))],
    )
    await callback.answer("Счёт отправлен в личку 💫")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_payment(message: Message) -> None:
    payload = message.successful_payment.invoice_payload or ""
    if not payload.startswith("title:"):
        await message.answer("Оплата получена, спасибо!")
        return
    title_id = int(payload.split(":")[1])
    title = await svc.get_title(title_id)
    await svc.deliver_stars_purchase(message.from_user.id, title_id)
    if title is None:
        await message.answer("Оплата получена, но титул больше недоступен. Напишите админу.")
        return
    text = (
        f"🌟 Спасибо за поддержку!\n\n{ui.title_html(title)} добавлен в коллекцию и надет.\n\n"
        + await profile_snippet(message.from_user.id)
    )
    await message.answer(text, reply_markup=ui.back_menu())
