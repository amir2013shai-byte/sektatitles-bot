"""Рынок: аукцион титулов и обмен между участниками."""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import economy
import services as svc
import ui
from catalog import RARITIES

router = Router(name="market")


def _int(raw: str | None, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ аукцион
async def auction_text() -> str:
    rows = await economy.auctions_list()
    if not rows:
        return ("<b>💎 Аукцион</b>\n\nСейчас нет активных лотов.\n"
                "<i>Выставить свой титул: /auction sell КОД СТАРТ_ЦЕНА ЧАСЫ</i>")
    lines = ["<b>💎 Аукцион титулов</b>\n"]
    for row in rows:
        top = f"{row['top_bid']} 🪙" if row["top_bid"] else "ставок нет"
        lines.append(
            f"{row['title_emoji']} <b>{html.escape(row['title_name'])}</b> "
            f"({RARITIES.get(row['title_rarity'], {}).get('label', row['title_rarity'])})\n"
            f"    лот #{row['id']} · старт {row['start_price']} 🪙 · топ: {top}\n"
            f"    до {row['ends_at'][:16].replace('T', ' ')} UTC"
        )
    lines.append("\nСтавка: <code>/bid НОМЕР_ЛОТА СУММА</code>")
    lines.append("Выставить свой титул: <code>/auction sell КОД СТАРТ ЧАСЫ</code>")
    return "\n".join(lines)


@router.message(Command("auction", "аукцион"))
async def cmd_auction(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if parts and parts[0].lower() in ("sell", "выставить"):
        await _create_auction(message, parts[1:])
        return
    if len(parts) >= 2 and parts[0].lower() in ("cancel", "отмена"):
        auction_id = _int(parts[1])
        ok, reason = await economy.cancel_auction(auction_id, message.from_user.id)
        await message.answer("✅ Лот отменён, титул возвращён." if ok else f"❌ {reason}")
        return
    if len(parts) >= 2 and parts[0].lower() in ("close", "закрыть"):
        await _close_from_command(message, _int(parts[1]))
        return
    await message.answer(await auction_text(), reply_markup=ui.auction_keyboard(
        await economy.auctions_list()))


@router.callback_query(F.data == "nav:auction")
async def cb_auction(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        await auction_text(), reply_markup=ui.auction_keyboard(await economy.auctions_list()))
    await callback.answer()


async def _create_auction(message: Message, parts: list[str]) -> None:
    if len(parts) < 3:
        await message.answer(
            "Формат: <code>/auction sell CODE СТАРТ_ЦЕНА ЧАСЫ</code>\n"
            "Только ваш титул; титул будет зарезервирован до продажи или отмены.\n"
            "Пример: <code>/auction sell gamer 1000 24</code>"
        )
        return
    title = await svc.get_title_by_code(parts[0])
    if title is None:
        await message.answer("Титул не найден. Список: /titles")
        return
    start, hours = _int(parts[1]), _int(parts[2], 24)
    ok, reason, auction_id = await economy.create_auction(
        message.from_user.id, title["id"], start, hours)
    if not ok:
        await message.answer(f"❌ {reason}")
        return
    await message.answer(
        f"💎 Лот #{auction_id} создан: {ui.title_html(title)}\n"
        f"Старт: <b>{start}</b> 🪙 · срок: <b>{hours}</b> ч.\n"
        "Титул зарезервирован; если ставок не будет, он вернётся вам.\n\n"
        f"Ставки: <code>/bid {auction_id} {start}</code> · отмена без ставок: "
        f"<code>/auction cancel {auction_id}</code>"
    )


async def _close_from_command(message: Message, auction_id: int) -> None:
    from config import config

    auction = await economy.auction_info(auction_id)
    if auction is None or auction["closed"]:
        await message.answer("Лот не найден или уже закрыт.")
        return
    if auction["seller_id"] != message.from_user.id and not config.is_owner_user(message.from_user):
        await message.answer("⛔️ Закрыть этот лот может только продавец или владелец бота.")
        return
    result = await economy.close_auction(auction_id)
    if result is None:
        await message.answer("Лот уже закрыт.")
    elif result.get("sold"):
        await message.answer(
            f"💎 Лот #{auction_id} завершён: титул у покупателя, "
            f"продавцу начислено {result['payout']} 🪙 (комиссия {result['fee']} 🪙)."
        )
    else:
        await message.answer(f"Лот #{auction_id} закрыт; титул {'возвращён продавцу' if result.get('returned') else 'не продан'}.")


@router.message(Command("bid", "ставка"))
async def cmd_bid(message: Message, command: CommandObject) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer("Формат: <code>/bid НОМЕР_ЛОТА СУММА</code>")
        return
    auction_id, amount = _int(parts[0]), _int(parts[1])
    result = await economy.place_bid(auction_id, message.from_user.id, amount)
    if not result["ok"]:
        await message.answer(f"❌ {result['error']}")
        return
    await message.answer(
        f"💎 Ставка принята: <b>{amount}</b> 🪙 на лот #{auction_id}.\n"
        f"Если вас перебьют — деньги вернутся автоматически."
    )


@router.callback_query(F.data.startswith("bid:"))
async def cb_bid(callback: CallbackQuery) -> None:
    _, raw_id, raw_amount = callback.data.split(":")
    auction_id, amount = int(raw_id), int(raw_amount)
    await svc.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    result = await economy.place_bid(auction_id, callback.from_user.id, amount)
    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return
    await callback.answer(f"Ставка {amount} 🪙 принята!")
    await callback.message.edit_text(
        f"💎 Лот #{auction_id}: лидирует {html.escape(callback.from_user.first_name or 'Участник')} "
        f"со ставкой <b>{amount}</b> 🪙",
        reply_markup=ui.auction_keyboard(await economy.auctions_list()),
    )


@router.message(Command("closeauction"))
async def cmd_close_auction(message: Message, command: CommandObject) -> None:
    auction_id = _int((command.args or "").strip())
    if not auction_id:
        await message.answer("Формат: <code>/closeauction НОМЕР</code>")
        return
    await _close_from_command(message, auction_id)


# -------------------------------------------------------------------- обмен
@router.message(Command("trade", "обмен"))
async def cmd_trade(message: Message, command: CommandObject) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    parts = (command.args or "").split()
    if len(parts) < 3:
        await message.answer(
            "<b>🔄 Обмен титулами</b>\n\n"
            "Формат: <code>/trade @партнёр МОЙ_ТИТУЛ ЕГО_ТИТУЛ</code>\n"
            "Пример: <code>/trade @durov gamer king</code>\n\n"
            "Список своих титулов: /profile → «Коллекция»"
        )
        return
    to_user = await svc.resolve_username(parts[0])
    if to_user is None:
        await message.answer("Не нашёл такого пользователя — он должен хотя бы раз написать боту.")
        return
    mine = await svc.get_title_by_code(parts[1])
    theirs = await svc.get_title_by_code(parts[2])
    if mine is None or theirs is None:
        await message.answer("Один из титулов не найден. Список: /titles")
        return
    ok, reason, trade_id = await economy.propose_trade(
        message.chat.id, message.from_user.id, to_user, mine["id"], theirs["id"])
    if not ok:
        await message.answer(f"❌ {reason}")
        return
    await message.answer(
        f"🔄 <b>Предложение обмена</b>\n\n"
        f"{html.escape(message.from_user.first_name or 'Участник')} отдаёт {ui.title_html(mine)}\n"
        f"и хочет получить {ui.title_html(theirs)}\n\n"
        f"Подтвердить может только получатель.",
        reply_markup=ui.trade_keyboard(trade_id),
    )


@router.callback_query(F.data.startswith("trade:"))
async def cb_trade(callback: CallbackQuery) -> None:
    _, action, trade_id = callback.data.split(":")
    trade_id = int(trade_id)
    if action == "accept":
        result = await economy.accept_trade(trade_id, callback.from_user.id)
        if not result["ok"]:
            await callback.answer(result["error"], show_alert=True)
            return
        trade = result["trade"]
        a = await svc.get_title(trade["from_title"])
        b = await svc.get_title(trade["to_title"])
        await callback.message.edit_text(
            f"🔄 <b>Обмен состоялся</b>\n\n"
            f"Вы получили {ui.title_html(a)}\n"
            f"Партнёр получил {ui.title_html(b)}"
        )
        await callback.answer("Обмен выполнен")
        return
    if action == "decline":
        ok = await economy.decline_trade(trade_id, callback.from_user.id)
        await callback.answer("Отклонено" if ok else "Не получилось")
        if ok:
            await callback.message.edit_text("🔄 Обмен отклонён.")
        return
    await callback.answer()


# ------------------------------------------------------------------ аренда
async def rentals_text() -> str:
    rows = await economy.rentals_list()
    if not rows:
        return (
            "<b>⏳ Аренда титулов</b>\n\nПредложений сейчас нет.\n"
            "Сдать свой титул: <code>/rent sell CODE ЦЕНА ЧАСЫ</code>"
        )
    lines = ["<b>⏳ Аренда титулов</b>\n"]
    for row in rows:
        owner = row["owner_name"] or (f"@{row['owner_username']}" if row["owner_username"] else str(row["owner_id"]))
        if row["state"] == "open":
            lines.append(
                f"▫️ #{row['id']} {row['title_emoji']} <b>{html.escape(row['title_name'])}</b>\n"
                f"    {row['price']} 🪙 · {row['duration_hours']} ч. · владелец {html.escape(owner)}"
            )
        else:
            lines.append(
                f"🟢 #{row['id']} {row['title_emoji']} <b>{html.escape(row['title_name'])}</b> — "
                f"аренда до {row['ends_at'][:16].replace('T', ' ')} UTC"
            )
    lines.append("\nНажмите кнопку, чтобы арендовать. Владелец может отменить свой лот до аренды.")
    lines.append("Сдать свой титул: <code>/rent sell CODE ЦЕНА ЧАСЫ</code>")
    return "\n".join(lines)


@router.message(Command("rentals", "аренда"))
async def cmd_rentals(message: Message) -> None:
    rows = await economy.rentals_list()
    await message.answer(await rentals_text(), reply_markup=ui.rentals_keyboard(rows))


@router.callback_query(F.data == "nav:rentals")
async def cb_rentals(callback: CallbackQuery) -> None:
    rows = await economy.rentals_list()
    await callback.message.edit_text(await rentals_text(), reply_markup=ui.rentals_keyboard(rows))
    await callback.answer()


@router.message(Command("rent"))
async def cmd_rent(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if not parts:
        rows = await economy.rentals_list()
        await message.answer(await rentals_text(), reply_markup=ui.rentals_keyboard(rows))
        return
    action = parts[0].lower()
    if action in ("sell", "сдать"):
        if len(parts) < 4:
            await message.answer("Формат: <code>/rent sell CODE ЦЕНА ЧАСЫ</code>\n"
                                 "Пример: <code>/rent sell gamer 300 24</code>")
            return
        title = await svc.get_title_by_code(parts[1])
        if title is None:
            await message.answer("Титул не найден. Список: /titles")
            return
        price, hours = _int(parts[2]), _int(parts[3])
        ok, reason, rental_id = await economy.create_rental(
            message.from_user.id, title["id"], price, hours)
        if not ok:
            await message.answer(f"❌ {reason}")
            return
        await message.answer(
            f"⏳ Предложение аренды #{rental_id} создано: {ui.title_html(title)}\n"
            f"Цена: <b>{price}</b> 🪙 · срок: <b>{hours}</b> ч.\n"
            "Титул зарезервирован; отменить до аренды: "
            f"<code>/rent cancel {rental_id}</code>"
        )
        return
    if action in ("buy", "арендовать"):
        if len(parts) < 2:
            await message.answer("Формат: <code>/rent buy ID</code>")
            return
        await _start_rental_reply(message, _int(parts[1]))
        return
    if action in ("cancel", "отменить"):
        if len(parts) < 2:
            await message.answer("Формат: <code>/rent cancel ID</code>")
            return
        ok, reason = await economy.cancel_rental(_int(parts[1]), message.from_user.id)
        await message.answer("✅ Предложение отменено, титул возвращён." if ok else f"❌ {reason}")
        return
    await message.answer("Используйте /rentals, /rent sell CODE ЦЕНА ЧАСЫ, /rent buy ID или /rent cancel ID.")


async def _start_rental_reply(message: Message, rental_id: int) -> None:
    result = await economy.start_rental(rental_id, message.from_user.id)
    if not result["ok"]:
        await message.answer(f"❌ {result['error']}")
        return
    rental = result["rental"]
    title = await svc.get_title(rental["title_id"])
    await message.answer(
        f"⏳ Вы арендовали {ui.title_html(title)} на <b>{rental['duration_hours']}</b> ч.\n"
        f"Титул автоматически вернётся владельцу. Срок до {result['ends_at'][:16].replace('T', ' ')} UTC."
    )


@router.callback_query(F.data.startswith("rent:"))
async def cb_rent(callback: CallbackQuery) -> None:
    rental_id = _int(callback.data.split(":", 1)[1])
    result = await economy.start_rental(rental_id, callback.from_user.id)
    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return
    rental = result["rental"]
    title = await svc.get_title(rental["title_id"])
    await callback.answer("Аренда оформлена!", show_alert=True)
    await callback.message.edit_text(
        f"⏳ Вы арендовали {ui.title_html(title)} на <b>{rental['duration_hours']}</b> ч.\n"
        "Титул автоматически вернётся владельцу после окончания срока."
    )
