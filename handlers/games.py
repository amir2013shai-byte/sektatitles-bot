"""Игры: казино, дуэли, крафт, лутбоксы."""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import economy
import services as svc
import ui
from catalog import RARITIES

router = Router(name="games")


def _int(raw: str | None, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


async def _user_link(bot, user_id: int) -> str:
    try:
        chat = await bot.get_chat(user_id)
        name = html.escape(chat.full_name or chat.username or str(user_id))
        return f'<a href="tg://user?id={user_id}">{name}</a>'
    except Exception:
        return f"<code>{user_id}</code>"


# ------------------------------------------------------------------- казино
@router.message(Command("dice", "кубик", "казино"))
async def cmd_dice(message: Message, command: CommandObject) -> None:
    stake = _int((command.args or "").split()[0] if command.args else 0)
    if not stake:
        await message.answer(
            "<b>🎲 Казино</b>\n\n"
            "Ваш кубик против кубика бота. Больше — победа, выплата ×2. Ничья — проигрыш.\n\n"
            "Ставка: <code>/dice 100</code> (от 10 до 10 000 🪙)"
        )
        return
    result = await economy.casino_dice(message.from_user.id, stake)
    if not result["ok"]:
        await message.answer(f"❌ {result['error']}")
        return
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    dice = "⚀⚁⚂⚃⚄⚅"
    head = "🟢 <b>Победа!</b>" if result["win"] else "🔴 <b>Проигрыш</b>"
    await message.answer(
        f"{head}\n\n"
        f"Вы: {dice[result['player'] - 1]} ({result['player']}) · "
        f"Бот: {dice[result['bot'] - 1]} ({result['bot']})\n"
        f"Итог: <b>{'+' if result['delta'] > 0 else ''}{result['delta']}</b> 🪙\n"
        f"Баланс: <b>{result['balance']}</b> 🪙"
    )


@router.message(Command("coin", "монетка"))
async def cmd_coin(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    stake = _int(parts[0]) if parts else 0
    side = "tails" if len(parts) > 1 and parts[1].lower() in ("решка", "tails", "т") else "heads"
    if not stake:
        await message.answer(
            "<b>🪙 Монетка</b>\n\nУгадайте сторону: выплата ×1.9\n\n"
            "<code>/coin 100</code> — орёл\n<code>/coin 100 решка</code> — решка"
        )
        return
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    result = await economy.casino_coin(message.from_user.id, stake, side)
    if not result["ok"]:
        await message.answer(f"❌ {result['error']}")
        return
    flip = "🦅 орёл" if result["flip"] == "heads" else "🌰 решка"
    head = "🟢 <b>Угадали!</b>" if result["win"] else "🔴 <b>Не угадали</b>"
    await message.answer(
        f"{head}\n\nВыпало: {flip}\n"
        f"Итог: <b>{'+' if result['delta'] > 0 else ''}{result['delta']}</b> 🪙\n"
        f"Баланс: <b>{result['balance']}</b> 🪙"
    )


# -------------------------------------------------------------------- дуэль
@router.message(Command("duel", "дуэль"))
async def cmd_duel(message: Message, command: CommandObject) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    parts = (command.args or "").split()
    if not parts or not message.reply_to_message and len(parts) < 2:
        await message.answer(
            "<b>⚔️ Дуэль</b>\n\n"
            "Вызов с ставкой: победитель забирает весь банк.\n\n"
            "• ответом на сообщение: <code>/duel 100</code>\n"
            "• по юзернейму: <code>/duel @user 100</code>"
        )
        return

    if message.reply_to_message and not parts[0].startswith("@"):
        opponent = message.reply_to_message.from_user.id
        stake = _int(parts[0])
        opponent_name = message.reply_to_message.from_user.first_name
    else:
        if not parts[0].startswith("@"):
            await message.answer("Укажите соперника: ответом на сообщение или <code>/duel @user 100</code>")
            return
        opponent = await svc.resolve_username(parts[0])
        stake = _int(parts[1]) if len(parts) > 1 else 0
        opponent_name = parts[0]
    if opponent is None:
        await message.answer("Не нашёл такого пользователя — он должен хотя бы раз написать боту.")
        return

    ok, reason, duel_id = await economy.create_duel(
        message.chat.id, message.from_user.id, opponent, stake)
    if not ok:
        await message.answer(f"❌ {reason}")
        return

    await message.answer(
        f"⚔️ <b>Дуэль!</b>\n\n"
        f"{await _user_link(message.bot, message.from_user.id)} вызывает "
        f"{await _user_link(message.bot, opponent)}\n"
        f"Ставка: <b>{stake}</b> 🪙 · банк: <b>{stake * 2}</b> 🪙\n\n"
        f"<i>У соперника 2 минуты, чтобы принять вызов.</i>",
        reply_markup=ui.duel_keyboard(duel_id),
    )


@router.callback_query(F.data.startswith("duel:"))
async def cb_duel(callback: CallbackQuery) -> None:
    _, action, duel_id = callback.data.split(":", 2)
    duel_id = int(duel_id)
    user = callback.from_user

    if action == "accept":
        result = await economy.accept_duel(duel_id, user.id)
        if not result["ok"]:
            await callback.answer(result["error"], show_alert=True)
            return
        dice = "⚀⚁⚂⚃⚄⚅"
        winner_tag = "🎉 Победитель: " + await _user_link(callback.bot, result["winner"])
        await callback.message.edit_text(
            f"⚔️ <b>Дуэль завершена</b>\n\n"
            f"{await _user_link(callback.bot, result['challenger'])}: "
            f"{dice[result['c_roll'] - 1]} ({result['c_roll']})\n"
            f"{await _user_link(callback.bot, result['opponent'])}: "
            f"{dice[result['o_roll'] - 1]} ({result['o_roll']})\n\n"
            f"{winner_tag} получает <b>{result['pot']}</b> 🪙"
        )
        await callback.answer("Дуэль завершена")
        return

    if action == "decline":
        ok = await economy.decline_duel(duel_id, user.id)
        await callback.answer("Вызов отклонён" if ok else "Не получилось")
        if ok:
            await callback.message.edit_text("⚔️ Дуэль отменена, ставка возвращена.")
        return

    await callback.answer()


# -------------------------------------------------------------------- крафт
@router.message(Command("craft", "крафт"))
async def cmd_craft(message: Message, command: CommandObject) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    rarity = (command.args or "").strip().lower()
    if rarity not in economy.RARITY_ORDER[:-1]:
        owned = await svc.inventory(message.from_user.id)
        counts = {r: sum(1 for t in owned if t["rarity"] == r) for r in economy.RARITY_ORDER}
        lines = ["<b>⚗️ Крафт титулов</b>\n",
                 f"3 титула одной редкости → 1 случайный титул следующей.\n"]
        for r in economy.RARITY_ORDER[:-1]:
            info = RARITIES[r]
            lines.append(f"{info['dot']} {info['label']}: <b>{counts[r]}</b>/3")
        lines.append("\nЗапуск: <code>/craft common</code> · <code>/craft rare</code> · <code>/craft epic</code> · <code>/craft legendary</code>")
        await message.answer("\n".join(lines))
        return

    result = await economy.craft(message.from_user.id, rarity)
    if not result["ok"]:
        await message.answer(f"❌ {result['error']}")
        return
    extra = "\n<i>Такой титул у вас уже был — дубликат не добавлен.</i>" if result["duplicate"] else ""
    await message.answer(
        f"⚗️ <b>Крафт удался!</b>\n\n"
        f"Разобрано: {', '.join(result['spent'])}\n"
        f"Получено: {ui.title_html(result['title'])} "
        f"({RARITIES[result['rarity']]['label']}){extra}"
    )


# ------------------------------------------------------------------ лутбокс
@router.message(Command("lootbox", "лутбокс", "box"))
async def cmd_lootbox(message: Message) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    result = await economy.lootbox(message.from_user.id)
    if not result["ok"]:
        await message.answer(
            f"❌ {result['error']}\n\n"
            f"<b>📦 Лутбокс</b> — {economy.LOOTBOX_PRICE} 🪙\n"
            "Шансы: обычный 55% · редкий 27% · эпический 12% · легендарный 5% · мифический 1%"
        )
        return
    if result.get("refund"):
        await message.answer(
            f"📦 В лутбоксе оказалась пустота — все титулы этой редкости у вас уже есть.\n"
            f"Возврат: <b>{result['coins']}</b> 🪙"
        )
        return
    rarity = RARITIES[result["rarity"]]
    note = ""
    if result["duplicate"]:
        note = f"\n<i>Дубликат — компенсация {result['compensation']} 🪙</i>"
    await message.answer(
        f"📦 <b>Лутбокс открыт!</b>\n\n"
        f"{rarity['dot']} {rarity['label']} {rarity['stars']}\n"
        f"{ui.title_html(result['title'])}{note}\n\n"
        f"Баланс: <b>{result.get('balance', '—')}</b> 🪙"
    )
