"""Админ-панель: массовая выдача, промокоды, каталог титулов, премиум-эмодзи."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import services as svc
import ui
from catalog import RARITIES
from config import config
from handlers.common import can_manage, is_owner

router = Router(name="admin")

MAX_BULK = 5000  # предохранитель от случайной рассылки на весь мир


class Mass(StatesGroup):
    title = State()
    audience = State()
    confirm = State()


class PromoNew(StatesGroup):
    code = State()
    kind = State()
    value = State()
    uses = State()


class TitleNew(StatesGroup):
    data = State()


async def guard(message: Message) -> bool:
    """Владелец бота или админ чата: выдача титулов, массовые рассылки."""
    if await can_manage(message.bot, message):  # type: ignore[arg-type]
        return True
    await message.answer(
        "⛔️ Команда доступна только администраторам чата (или владельцу бота).\n"
        "Проверить свои права: /whoami"
    )
    return False


async def owner_guard(message: Message) -> bool:
    """ТОЛЬКО владелец бота: промокоды и каталог титулов."""
    if is_owner(message.from_user):
        return True
    if config.allow_chat_admin_promos and await can_manage(message.bot, message):  # type: ignore[arg-type]
        return True
    await message.answer(
        "⛔️ Эта команда доступна только владельцу бота.\n"
        "Проверить свои права: /whoami"
    )
    return False


# ------------------------------------------------------------------- админка
@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not await guard(message):
        return
    await message.answer(await admin_home_text(), reply_markup=ui.admin_menu())


async def admin_home_text() -> str:
    return (
        "<b>⚙️ Админ-панель</b>\n\n"
        "📢 <b>Массовая выдача</b> — выдать титул всем в чате, админам, всем пользователям бота "
        "или покупателям.\n\n"
        "🎯 <b>/grant</b> — выдать одному (ответом на сообщение или <code>/grant @user code</code>)\n"
        "🧾 <b>/newpromo</b> — создать промокод <i>(только владелец)</i>\n"
        "🎭 <b>/addtitle</b> — добавить титул в каталог <i>(только владелец)</i>\n"
        "😎 <b>/loademoji short_name</b> — импортировать премиум-эмодзи из набора\n"
        "🗂 <b>/sync</b> — подтянуть админов чата в базу (для массовой выдачи)\n"
        "📊 <b>/stats</b> — цифры"
    )


@router.callback_query(F.data == "nav:admin")
async def cb_admin(callback: CallbackQuery) -> None:
    if not await can_manage(callback.bot, callback.message, callback.from_user):  # type: ignore[arg-type]
        await callback.answer("Только для админов", show_alert=True)
        return
    await callback.message.edit_text(await admin_home_text(), reply_markup=ui.admin_menu())
    await callback.answer()


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.\n\n" + await admin_home_text(), reply_markup=ui.admin_menu())
    await callback.answer()


# --------------------------------------------------------------- статистика
@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not await guard(message):
        return
    s = await svc.stats()
    await message.answer(
        "<b>📊 Статистика</b>\n\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n"
        f"🎭 Титулов в каталоге: <b>{s['titles']}</b>\n"
        f"🎖 Выдано титулов: <b>{s['issued']}</b>\n"
        f"🎁 Активных промокодов: <b>{s['promos']}</b>"
    )


# ------------------------------------------------------- выдача одному юзеру
async def _target_from_message(message: Message, args: list[str]) -> tuple[int | None, str | None]:
    """Извлекает (user_id, code) из аргументов команды. Поддерживает @username и ответ на сообщение."""
    code = None
    target = None
    for arg in args:
        if arg.startswith("@"):
            target = await svc.resolve_username(arg)
        elif re.fullmatch(r"\d{5,}", arg):
            target = int(arg)
        else:
            code = arg
    if target is None and message.reply_to_message:
        target = message.reply_to_message.from_user.id
    return target, code


@router.message(Command("grant", "выдать"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    if not await guard(message):
        return
    target, code = await _target_from_message(message, (command.args or "").split())
    if not code:
        await message.answer(
            "Формат:\n<code>/grant CODE</code> (ответом на сообщение)\n"
            "<code>/grant @username CODE</code>\n\n"
            "Пример: <code>/grant @durov king</code>\nКоды титулов: /titles"
        )
        return
    title = await svc.get_title_by_code(code)
    if title is None:
        await message.answer("Титул с таким кодом не найден. Список: /titles")
        return
    if target is None:
        await message.answer("Не понял, кому выдавать. Ответьте на сообщение или укажите @username.")
        return

    await svc.ensure_user(target)
    new = await svc.give_title(target, title["id"], source=f"grant:{message.from_user.id}")
    if message.chat.type in ("group", "supergroup"):
        await _sync_custom_title(message.bot, message.chat.id, target, title)  # type: ignore[arg-type]
    await message.answer(
        ("✅ Выдан новый титул " if new else "ℹ️ Титул уже был у пользователя: ")
        + f"{ui.title_html(title)} → <code>{target}</code>"
    )


@router.message(Command("revoke", "забрать"))
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    if not await guard(message):
        return
    target, code = await _target_from_message(message, (command.args or "").split())
    if not code or target is None:
        await message.answer("Формат: <code>/revoke CODE</code> ответом на сообщение.")
        return
    title = await svc.get_title_by_code(code)
    if title is None:
        await message.answer("Титул не найден. Список: /titles")
        return
    ok = await svc.revoke_title(target, title["id"])
    await message.answer("🗑 Титул забран." if ok else "У пользователя не было этого титула.")


@router.message(Command("titles"))
async def cmd_titles(message: Message) -> None:
    # Публичный список магазина не раскрывает наградные/секретные титулы.
    titles = await svc.all_titles()
    lines = ["<b>📚 Титулы в каталоге</b>\n"]
    for t in titles:
        lines.append(
            f"<code>{t['code']}</code> — {t['emoji']} {ui.esc(t['name'])} · "
            f"{RARITIES.get(t['rarity'], {}).get('label', t['rarity'])} · {ui.price_line(t)}"
        )
    text = "\n".join(lines)
    await message.answer(text[:3900])


# --------------------------------------------------------- массовая выдача
@router.message(Command("mass", "масс"))
async def cmd_mass(message: Message, state: FSMContext) -> None:
    if not await guard(message):
        return
    await state.set_state(Mass.title)
    titles = await svc.all_titles(include_rewards=True)
    await message.answer(
        "<b>📢 Массовая выдача титула</b>\n\nШаг 1/3 — выберите титул:",
        reply_markup=ui.mass_titles_keyboard(titles, 0),
    )


@router.callback_query(F.data == "adm:mass")
async def cb_mass(callback: CallbackQuery, state: FSMContext) -> None:
    if not await can_manage(callback.bot, callback.message, callback.from_user):  # type: ignore[arg-type]
        await callback.answer("Только для админов", show_alert=True)
        return
    await state.set_state(Mass.title)
    titles = await svc.all_titles(include_rewards=True)
    await callback.message.edit_text(
        "<b>📢 Массовая выдача титула</b>\n\nШаг 1/3 — выберите титул:",
        reply_markup=ui.mass_titles_keyboard(titles, 0),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mtp:"))
async def cb_mass_page(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    titles = await svc.all_titles(include_rewards=True)
    await callback.message.edit_reply_markup(reply_markup=ui.mass_titles_keyboard(titles, page))
    await callback.answer()


@router.callback_query(F.data.startswith("mt:"))
async def cb_mass_pick_title(callback: CallbackQuery, state: FSMContext) -> None:
    title_id = int(callback.data.split(":")[1])
    await state.update_data(title_id=title_id)
    await state.set_state(Mass.audience)
    title = await svc.get_title(title_id)
    chat_id = callback.message.chat.id
    in_chat = len(await svc.bulk_targets(chat_id, "chat")) if chat_id < 0 else 0
    await callback.message.edit_text(
        f"Титул: {ui.title_html(title)}\n\n"
        f"Шаг 2/3 — кому выдаём?\n"
        f"👥 В этом чате бот знает <b>{in_chat}</b> чел. "
        f"(если мало — выполните /sync)",
        reply_markup=ui.mass_audience_keyboard(title_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ma:"))
async def cb_mass_audience(callback: CallbackQuery, state: FSMContext) -> None:
    _, title_id, mode = callback.data.split(":")
    title_id = int(title_id)
    chat_id = callback.message.chat.id

    if mode == "admins":
        try:
            admins = await callback.bot.get_chat_administrators(chat_id)  # type: ignore[union-attr]
            targets = [a.user.id for a in admins if not a.user.is_bot]
        except Exception:
            targets = []
    else:
        targets = await svc.bulk_targets(chat_id, mode)

    targets = [t for t in targets if t > 0][:MAX_BULK]
    await state.update_data(title_id=title_id, mode=mode, targets=targets)
    await state.set_state(Mass.confirm)

    title = await svc.get_title(title_id)
    await callback.message.edit_text(
        f"Титул: {ui.title_html(title)}\n"
        f"Аудитория: <b>{mode}</b>\n"
        f"Получателей: <b>{len(targets)}</b>\n\n"
        f"Шаг 3/3 — подтвердить?",
        reply_markup=ui.mass_confirm_keyboard(title_id, mode, len(targets)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mc:"))
async def cb_mass_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await can_manage(callback.bot, callback.message, callback.from_user):  # type: ignore[arg-type]
        await callback.answer("Только для админов", show_alert=True)
        return
    data = await state.get_data()
    title_id = int(callback.data.split(":")[1])
    targets = data.get("targets") or []
    mode = data.get("mode", "chat")
    if not targets:
        await callback.answer("Пустой список получателей. Сделайте /sync и попробуйте снова.", show_alert=True)
        return

    await callback.message.edit_text("⏳ Выдаю титулы…")
    given, total = await svc.bulk_give(
        targets, title_id,
        chat_id=callback.message.chat.id,
        actor_id=callback.from_user.id,
        source=f"mass:{mode}",
    )
    if callback.message.chat.type in ("group", "supergroup"):
        title = await svc.get_title(title_id)
        for uid in targets[:50]:  # дублируем настоящие титулы админам
            await _sync_custom_title(callback.bot, callback.message.chat.id, uid, title)  # type: ignore[arg-type]

    await state.clear()
    await callback.message.edit_text(
        f"✅ Готово!\n\n"
        f"Титул: <b>{ui.esc((await svc.get_title(title_id))['name'])}</b>\n"
        f"Обработано: <b>{total}</b>\n"
        f"Новых выдач: <b>{given}</b>\n"
        f"У остальных титул уже был.",
        reply_markup=ui.admin_menu(),
    )
    await callback.answer("Выдано!")


async def _sync_custom_title(bot: Bot, chat_id: int, user_id: int, title) -> None:
    """Дублирует титул в настоящий «custom title» администратора чата, если это админ."""
    if title is None:
        return
    try:
        await bot.set_chat_administrator_custom_title(chat_id, user_id, title["name"][:16])
    except Exception:
        pass


# ---------------------------------------------------------------- /sync
@router.message(Command("sync"))
async def cmd_sync(message: Message) -> None:
    if not await guard(message):
        return
    chat_id = message.chat.id
    if chat_id >= 0:
        await message.answer("Команда работает в группе.")
        return
    added = 0
    try:
        admins = await message.bot.get_chat_administrators(chat_id)  # type: ignore[union-attr]
        for a in admins:
            if a.user.is_bot:
                continue
            await svc.ensure_user(a.user.id, a.user.username, a.user.first_name)
            await svc.touch_chat(chat_id, a.user.id)
            added += 1
    except Exception as exc:
        await message.answer(f"Не смог получить админов: {exc}")
        return
    total = len(await svc.bulk_targets(chat_id, "chat"))
    await message.answer(f"🗂 Добавлено админов: {added}. Всего в базе чата: {total}.")


# ---------------------------------------------------------------- промокоды
@router.message(Command("newpromo"))
async def cmd_newpromo(message: Message, command: CommandObject, state: FSMContext) -> None:
    if not await owner_guard(message):
        return
    await state.set_state(PromoNew.code)
    await message.answer(
        "<b>🧾 Новый промокод</b>\n\n"
        "Шаг 1/4 — пришлите код (латиница/цифры), например <code>DRAGON2026</code>.\n"
        "Или отмените: /admin",
        reply_markup=ui.promo_confirm_none(),
    )


@router.message(PromoNew.code)
async def promo_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
        await message.answer("Код: 3–32 символа, латиница/цифры/_- . Попробуйте ещё раз.")
        return
    await state.update_data(code=code)
    await state.set_state(PromoNew.kind)
    await message.answer(
        "Шаг 2/4 — что даёт промокод?\n\n"
        "1 — монеты 🪙\n"
        "2 — титул 🎭\n"
        "3 — и монеты, и титул\n\n"
        "Пришлите цифру.",
        reply_markup=ui.promo_confirm_none(),
    )


@router.message(PromoNew.kind)
async def promo_kind(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    mapping = {"1": "coins", "2": "title", "3": "both", "монеты": "coins", "титул": "title"}
    kind = mapping.get(raw.lower())
    if kind is None:
        await message.answer("Пришлите 1, 2 или 3.")
        return
    await state.update_data(kind=kind)
    await state.set_state(PromoNew.value)
    if kind == "coins":
        await message.answer("Шаг 3/4 — сколько монет начислить? (например 500)")
    elif kind == "title":
        await message.answer("Шаг 3/4 — код титула (список: /titles), например <code>dragon</code>")
    else:
        await message.answer("Шаг 3/4 — пришлите в формате <code>КОД_ТИТУЛА монеты</code>, например <code>dragon 500</code>")


@router.message(PromoNew.value)
async def promo_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data["kind"]
    raw = (message.text or "").strip()
    coins, title_id = 0, None

    if kind == "coins":
        if not raw.isdigit():
            await message.answer("Нужно число монет.")
            return
        coins = int(raw)
    elif kind == "title":
        title = await svc.get_title_by_code(raw)
        if title is None:
            await message.answer("Титул не найден. Список: /titles")
            return
        title_id = title["id"]
    else:
        parts = raw.split()
        title = await svc.get_title_by_code(parts[0]) if parts else None
        if title is None or len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Формат: <code>dragon 500</code>")
            return
        title_id = title["id"]
        coins = int(parts[1])

    await state.update_data(coins=coins, title_id=title_id)
    await state.set_state(PromoNew.uses)
    await message.answer(
        "Шаг 4/4 — сколько активаций всего?\n"
        "Пришлите число или <code>0</code> для неограниченного количества."
    )


@router.message(PromoNew.uses)
async def promo_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно число (0 = без ограничений).")
        return
    uses = int(raw) or -1
    data = await state.get_data()
    await state.clear()

    await svc.create_promo(
        data["code"],
        kind=data["kind"],
        coins=data.get("coins", 0),
        title_id=data.get("title_id"),
        uses_left=uses,
        per_user=1,
        created_by=message.from_user.id,
    )
    title = await svc.get_title(data["title_id"]) if data.get("title_id") else None
    reward = []
    if data.get("coins"):
        reward.append(f"{data['coins']} 🪙")
    if title:
        reward.append(f"титул «{title['name']}»")
    await message.answer(
        "✅ <b>Промокод создан</b>\n\n"
        f"Код: <code>{data['code']}</code>\n"
        f"Награда: {', '.join(reward) or '—'}\n"
        f"Активаций: {'∞' if uses == -1 else uses}\n\n"
        "Отправьте код в чат — бот сам его активирует, когда участник пришлёт код сообщением.",
        reply_markup=ui.admin_menu(),
    )


@router.callback_query(F.data == "adm:promos")
async def cb_promos(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user):
        await callback.answer("Промокоды доступны только владельцу бота", show_alert=True)
        return
    promos = await svc.promos_list()
    if not promos:
        text = "<b>🧾 Промокоды</b>\n\nПока нет ни одного. Создать: /newpromo"
    else:
        lines = ["<b>🧾 Промокоды</b>\n"]
        for p in promos:
            state = "🟢" if p["active"] else "⚪️"
            left = "∞" if p["uses_left"] == -1 else p["uses_left"]
            lines.append(f"{state} <code>{p['code']}</code> · {p['kind']} · осталось {left}")
        lines.append("\nСоздать новый: /newpromo\nОтключить: /delpromo КОД")
        text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=ui.admin_menu())
    await callback.answer()


@router.message(Command("delpromo"))
async def cmd_delpromo(message: Message, command: CommandObject) -> None:
    if not await owner_guard(message):
        return
    code = (command.args or "").strip()
    if not code:
        await message.answer("Формат: <code>/delpromo КОД</code>")
        return
    ok = await svc.deactivate_promo(code)
    await message.answer("🛑 Промокод отключён." if ok else "Такого промокода нет.")


# ------------------------------------------------------------- новый титул
@router.message(Command("addtitle"))
async def cmd_addtitle(message: Message, state: FSMContext) -> None:
    if not await owner_guard(message):
        return
    await state.set_state(TitleNew.data)
    await message.answer(
        "<b>🎭 Новый титул</b>\n\n"
        "Пришлите одной строкой:\n"
        "<code>код | название | редкость | эмодзи | цена_монеты | цена_звёзды | тираж</code>\n\n"
        "Пример:\n"
        "<code>cyber | Кибер-ниндзя | epic | 🥷 | 5000 | 0 | -1</code>\n\n"
        f"Редкости: {', '.join(RARITIES)}\nТираж: -1 = без ограничений",
        reply_markup=ui.promo_confirm_none(),
    )


@router.message(TitleNew.data)
async def addtitle_data(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 4:
        await message.answer("Нужно минимум: код | название | редкость | эмодзи")
        return
    code, name, rarity, emoji = parts[0], parts[1], parts[2].lower(), parts[3]
    if rarity not in RARITIES:
        await message.answer(f"Редкость должна быть одной из: {', '.join(RARITIES)}")
        return
    price = int(parts[4]) if len(parts) > 4 and parts[4].lstrip("-").isdigit() else 0
    stars = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
    stock = int(parts[6]) if len(parts) > 6 and parts[6].lstrip("-").isdigit() else -1

    title_id = await svc.add_title(code, name, rarity, emoji, price, stars, stock)
    await state.clear()
    title = await svc.get_title(title_id)
    await message.answer(
        f"✅ Титул добавлен: {ui.title_html(title)}\n"
        f"Цена: {ui.price_line(title)}\n\n"
        f"Выдать: <code>/grant {code}</code> или /mass",
        reply_markup=ui.admin_menu(),
    )


# ------------------------------------------------------- премиум-эмодзи
@router.message(Command("loademoji"))
async def cmd_loademoji(message: Message, command: CommandObject) -> None:
    if not await owner_guard(message):
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Импорт премиум-эмодзи из набора Telegram.\n\n"
            "1. Откройте стикерпак с премиум-эмодзи (tg://emoji?…) в Telegram.\n"
            "2. Скопируйте его имя из ссылки: <code>t.me/addemoji/ИМЯ_НАБОРА</code>\n"
            "3. Пришлите <code>/loademoji ИМЯ_НАБОРА</code>\n\n"
            "Пример: <code>/loademoji EmojiSetName</code>\n\n"
            "Эмодзи снова появятся в разделе «Эмодзи» — их видно тем, у кого есть Premium."
        )
        return
    try:
        sticker_set = await message.bot.get_sticker_set(raw)  # type: ignore[union-attr]
    except Exception as exc:
        await message.answer(f"Не нашёл набор <code>{raw}</code>: {exc}")
        return
    items = [(s.emoji, s.custom_emoji_id) for s in sticker_set.stickers if getattr(s, "custom_emoji_id", None)]
    added = await svc.add_emoji_set(sticker_set.name, items)
    await message.answer(
        f"✅ Импортировано {added} эмодзи из набора <b>{sticker_set.name}</b>.\n"
        "Проверьте: /style"
    )
