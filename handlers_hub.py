"""Навигация по новым разделам (кнопки главного меню) и обработчики казино/крафта."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

import db
import economy
import services as svc
import ui
from config import config
from handlers.common import can_manage
from handlers.games import _user_link

router = Router(name="hub")


@router.callback_query(F.data == "nav:casino")
async def cb_casino(callback: CallbackQuery) -> None:
    await svc.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    balance = int(await db.scalar("SELECT coins FROM users WHERE user_id = ?",
                                  (callback.from_user.id,), 0))
    await callback.message.edit_text(
        "<b>🎲 Казино</b>\n\n"
        "Кубик против бота: больше — победа, выплата ×2 (ничья — проигрыш).\n"
        "Монетка: угадал — ×1.9.\n\n"
        f"Ваш баланс: <b>{balance}</b> 🪙\n\n"
        "Своя ставка: <code>/dice 250</code> или <code>/coin 250 решка</code>",
        reply_markup=ui.casino_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cas:"))
async def cb_casino_play(callback: CallbackQuery) -> None:
    _, kind, raw = callback.data.split(":")
    stake = int(raw)
    if kind == "dice":
        result = await economy.casino_dice(callback.from_user.id, stake)
    else:
        result = await economy.casino_coin(callback.from_user.id, stake)
    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return
    if kind == "dice":
        dice = "⚀⚁⚂⚃⚄⚅"
        text = (f"{'🟢 Победа' if result['win'] else '🔴 Проигрыш'} · "
                f"вы {dice[result['player'] - 1]}{result['player']} : "
                f"бот {dice[result['bot'] - 1]}{result['bot']}\n"
                f"Итог: <b>{'+' if result['delta'] > 0 else ''}{result['delta']}</b> 🪙 · "
                f"баланс <b>{result['balance']}</b> 🪙")
    else:
        text = (f"{'🟢 Угадали' if result['win'] else '🔴 Не угадали'} · "
                f"{'орёл 🦅' if result['flip'] == 'heads' else 'решка 🌰'}\n"
                f"Итог: <b>{'+' if result['delta'] > 0 else ''}{result['delta']}</b> 🪙 · "
                f"баланс <b>{result['balance']}</b> 🪙")
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data == "nav:craft")
async def cb_craft(callback: CallbackQuery) -> None:
    owned = await svc.inventory(callback.from_user.id)
    counts = {r: sum(1 for t in owned if t["rarity"] == r)
              for r in economy.RARITY_ORDER}
    labels = {"common": "обычные ⚪️", "rare": "редкие 🔵", "epic": "эпические 🟣",
              "legendary": "легендарные 🟡", "mythic": "мифические 🔴"}
    lines = ["<b>⚗️ Крафт титулов</b>",
             f"3 титула одной редкости → 1 случайный титул следующей.\n"]
    for r, count in counts.items():
        lines.append(f"{labels[r]}: <b>{count}</b>")
    lines.append("\n<i>Надетый титул в крафте не участвует.</i>")
    await callback.message.edit_text("\n".join(lines), reply_markup=ui.craft_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("cr:"))
async def cb_craft_do(callback: CallbackQuery) -> None:
    rarity = callback.data.split(":")[1]
    result = await economy.craft(callback.from_user.id, rarity)
    if not result["ok"]:
        await callback.answer(result["error"], show_alert=True)
        return
    await callback.answer("Крафт удался!", show_alert=True)
    extra = "\n<i>Дубликат — не добавлен.</i>" if result["duplicate"] else ""
    await callback.message.edit_text(
        f"⚗️ <b>Крафт удался!</b>\n\nРазобрано: {', '.join(result['spent'])}\n"
        f"Получено: {ui.title_html(result['title'])}{extra}",
        reply_markup=ui.back_menu(),
    )


@router.callback_query(F.data == "nav:lootbox")
async def cb_lootbox(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        f"<b>📦 Лутбокс</b> — {economy.LOOTBOX_PRICE} 🪙\n\n"
        "Внутри случайный титул. Шансы:\n"
        "⚪️ обычный 55% · 🔵 редкий 27% · 🟣 эпический 12% · "
        "🟡 легендарный 5% · 🔴 мифический 1%\n\n"
        "Открыть: /lootbox",
        reply_markup=ui.back_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "nav:duels")
async def cb_duels(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>⚔️ Дуэли</b>\n\n"
        "Вызов участника на ставку: победитель забирает весь банк.\n\n"
        "• ответом на сообщение: <code>/duel 100</code>\n"
        "• по юзернейму: <code>/duel @user 100</code>\n\n"
        "Ставка от 10 🪙. У соперника 2 минуты на ответ.",
        reply_markup=ui.back_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "nav:trade")
async def cb_trade(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>🔄 Обмен титулами</b>\n\n"
        "Формат: <code>/trade @партнёр МОЙ_ТИТУЛ ЕГО_ТИТУЛ</code>\n"
        "Пример: <code>/trade @durov gamer king</code>\n\n"
        "Коды титулов: /titles",
        reply_markup=ui.back_menu(),
    )
    await callback.answer()
