"""Квесты: список видимых, прогресс, награды. Секретные не показываются."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import quests
import services as svc
import ui

router = Router(name="quests")


def _bar(progress: int, target: int, width: int = 10) -> str:
    filled = int(min(progress, target) / max(target, 1) * width)
    return "▰" * filled + "▱" * (width - filled)


async def quests_text(user_id: int) -> str:
    rows = await quests.visible_progress(user_id)
    done = sum(1 for q, progress, _ in rows if progress >= q.target)
    claimed = sum(1 for q, p, is_claimed in rows if is_claimed)
    lines = [
        "<b>🎯 Задания</b>",
        f"Выполнено: <b>{done}</b> из {len(rows)} · награды забраны: <b>{claimed}</b>\n",
    ]
    for quest, progress, is_claimed in rows:
        if is_claimed:
            lines.append(f"✅ <s>{quest.name}</s> — награда получена")
            continue
        if progress >= quest.target:
            lines.append(f"🎁 <b>{quest.name}</b> — готово к получению!")
        else:
            lines.append(
                f"▫️ <b>{quest.name}</b> — {quest.desc}\n"
                f"    {_bar(progress, quest.target)} {progress}/{quest.target}"
            )
    return "\n".join(lines)


async def quests_keyboard(user_id: int):
    return ui.quests_keyboard(await quests.visible_progress(user_id))


@router.message(Command("quests", "квесты", "tasks", "задания"))
async def cmd_quests(message: Message) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        await quests_text(message.from_user.id),
        reply_markup=await quests_keyboard(message.from_user.id),
    )


@router.callback_query(F.data == "nav:quests")
async def cb_quests(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        await quests_text(callback.from_user.id),
        reply_markup=await quests_keyboard(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("qc:"))
async def cb_quest_claim(callback: CallbackQuery) -> None:
    quest_id = callback.data.split(":", 1)[1]
    ok, reason, quest = await quests.claim(callback.from_user.id, quest_id)
    if not ok:
        texts = {
            "not_done": "Задание ещё не выполнено",
            "already": "Награда уже получена",
            "no_quest": "Задание не найдено",
        }
        await callback.answer(texts.get(reason, "Не получилось"), show_alert=True)
        return

    reward = []
    if quest.coins:
        reward.append(f"+{quest.coins} 🪙")
    if quest.title_code:
        title = await svc.get_title_by_code(quest.title_code)
        if title:
            reward.append(f"титул {ui.title_html(title)}")
    await callback.answer("Награда получена!")
    await callback.message.edit_text(
        f"🎁 <b>Награда получена!</b>\n\n"
        f"Задание: <b>{quest.name}</b>\n"
        f"Награда: {', '.join(reward) or '—'}\n\n" + await quests_text(callback.from_user.id),
        reply_markup=await quests_keyboard(callback.from_user.id),
    )
