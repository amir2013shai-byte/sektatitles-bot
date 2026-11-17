"""Общие хендлеры: регистрация, меню, профиль, топ, бонус, активация промокода."""
from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, ChatMemberAdministrator, ChatMemberOwner, Message

import services as svc
import ui
from config import config

router = Router(name="common")

ADMIN_STATUSES = (ChatMemberAdministrator, ChatMemberOwner)


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if config.is_owner(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return isinstance(member, ADMIN_STATUSES)


async def can_manage(bot: Bot, message: Message, user=None) -> bool:
    """Может ли пользователь управлять титулами (владелец или админ чата)."""
    user = user or message.from_user
    if config.is_owner_user(user):
        return True
    if message.chat.type in ("group", "supergroup"):
        return await is_chat_admin(bot, message.chat.id, user.id)
    return False


def is_owner(user) -> bool:
    """Только владелец бота (промокоды, каталог титулов)."""
    return config.is_owner_user(user)


HELP_TEXT = None  # заполняется ниже


HELP_TEXT = (
    "<b>🎖 Что умеет бот</b>\n\n"
    "• <b>Профиль</b> — ваш титул, монеты и коллекция\n"
    "• <b>Магазин</b> — титулы за монеты 🪙 и за звёзды Telegram ⭐\n"
    "• <b>Эмодзи</b> — свой эмодзи к любому титулу (в т.ч. премиум)\n"
    "• <b>Промокоды</b> — бонусные монеты и эксклюзивные титулы\n"
    "• <b>Бонус дня</b> — {daily} 🪙 каждый день\n\n"
    "Команды: /profile /shop /style /promo КОД /top /daily\n\n"
    "<b>Для админов чата:</b>\n"
    "/grant — выдать титул (в ответ на сообщение)\n"
    "/mass — массовая выдача\n"
    "/newpromo — создать промокод\n"
    "/addtitle — добавить титул\n"
    "/stats — статистика\n"
    "/whoami — проверить свои права"
)


def help_text() -> str:
    return HELP_TEXT.format(daily=config.daily_bonus)


async def send_menu(message: Message, text: str | None = None) -> None:
    admin = await can_manage(message.bot, message)  # type: ignore[arg-type]
    await message.answer(
        text or (
            f"Привет, <b>{ui.esc(message.from_user.first_name)}</b>! 👋\n\n"
            "Этот бот выдаёт <b>титулы</b>: покупай, получай по промокодам, "
            "меняй эмодзи и собирай коллекцию.\n\nВыбирай 👇"
        ),
        reply_markup=ui.main_menu(admin),
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await send_menu(message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(help_text(), reply_markup=ui.back_menu())


@router.message(Command("whoami", "myid", "ктоя"))
async def cmd_whoami(message: Message) -> None:
    u = message.from_user
    owner = config.is_owner_user(u)
    lines = [
        "<b>🪪 Кто я для бота</b>\n",
        f"ID: <code>{u.id}</code>",
        f"Юзернейм: @{ui.esc(u.username) if u.username else '—'}",
        f"Владелец бота: {'✅ да' if owner else '❌ нет'}",
        f"OWNER_IDS в конфиге: <code>{config.owner_ids or 'пусто'}</code>",
        f"OWNER_USERNAMES: <code>{', '.join(config.owner_usernames) or 'пусто'}</code>",
    ]
    if message.chat.type in ("group", "supergroup"):
        adm = await is_chat_admin(message.bot, message.chat.id, u.id)  # type: ignore[arg-type]
        lines.append(f"Админ этого чата: {'✅ да' if adm else '❌ нет'}")
    if not owner:
        lines.append("\n<i>Если вы владелец — добавьте свой ID в OWNER_IDS (или юзернейм в "
                     "OWNER_USERNAMES) в переменных окружения хостинга.</i>")
    await message.answer("\n".join(lines))


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await send_menu(message)


# ------------------------------------------------------------------ профиль
async def profile_text(user_id: int) -> str:
    user = await svc.get_user(user_id) or await svc.ensure_user(user_id)
    equipped = await svc.get_title(user["equipped_id"]) if user["equipped_id"] else None
    style = await svc.get_emoji(user["emoji_id"]) if user["emoji_id"] else None
    owned = len(await svc.inventory(user_id))

    if equipped is None:
        head = "🎭 <i>титул не надет</i>"
    else:
        override = (style["emoji"], style["custom_emoji_id"]) if style else None
        head = ui.title_html(equipped, override)

    return (
        f"<b>🎭 Профиль</b>\n\n"
        f"Имя: <b>{ui.esc(user['first_name'] or user['username'] or '—')}</b>\n"
        f"Титул: {head}\n"
        f"Монеты: <b>{user['coins']}</b> 🪙\n"
        f"Коллекция: <b>{owned}</b> титул(ов)\n"
    )


@router.callback_query(F.data == "nav:menu")
async def cb_menu(callback: CallbackQuery) -> None:
    admin = await can_manage(callback.bot, callback.message, callback.from_user)  # type: ignore[arg-type]
    await callback.message.edit_text(
        "Главное меню 👇", reply_markup=ui.main_menu(admin)
    )
    await callback.answer()


@router.message(Command("profile", "me"))
async def cmd_profile(message: Message) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(await profile_text(message.from_user.id), reply_markup=ui.main_menu(
        await can_manage(message.bot, message)  # type: ignore[arg-type]
    ))


@router.callback_query(F.data == "nav:profile")
async def cb_profile(callback: CallbackQuery) -> None:
    text = await profile_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=ui.back_menu())
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ---------------------------------------------------------------------- топ
@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    await message.answer(await top_text(), reply_markup=ui.back_menu())


@router.callback_query(F.data == "nav:top")
async def cb_top(callback: CallbackQuery) -> None:
    await callback.message.edit_text(await top_text(), reply_markup=ui.back_menu())
    await callback.answer()


async def top_text() -> str:
    by_coins = await svc.top_by_coins(5)
    by_titles = await svc.top_by_titles(5)

    lines = ["<b>🏆 Топ чата</b>\n", "<b>💰 Богачи</b>"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, row in enumerate(by_coins):
        lines.append(f"{medals[i]} {ui.user_link(row)} — {row['coins']} 🪙")
    if not by_coins:
        lines.append("<i>пока пусто</i>")

    lines.append("\n<b>🎭 Коллекционеры</b>")
    for i, row in enumerate(by_titles):
        lines.append(f"{medals[i]} {ui.user_link(row)} — {row['cnt']} 🎖")
    if not by_titles:
        lines.append("<i>пока пусто</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- бонус дня
@router.message(Command("daily", "bonus"))
async def cmd_daily(message: Message) -> None:
    await svc.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    ok, value = await svc.daily_bonus(message.from_user.id)
    if ok:
        await message.answer(f"🎁 Бонус дня: <b>+{value}</b> 🪙\nБаланс: <b>{await balance(message.from_user.id)}</b> 🪙")
    else:
        hours = max(1, value // 3600)
        await message.answer(f"⏳ Бонус уже получен. Следующий — через ~{hours} ч.")


@router.callback_query(F.data == "nav:daily")
async def cb_daily(callback: CallbackQuery) -> None:
    ok, value = await svc.daily_bonus(callback.from_user.id)
    if ok:
        await callback.answer(f"+{value} монет!", show_alert=True)
    else:
        hours = value // 3600 or 1
        await callback.answer(f"Уже получено. Возвращайтесь через ~{hours} ч.", show_alert=True)


# ------------------------------------------------------------------ промокод
@router.callback_query(F.data == "nav:promo")
async def cb_promo(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>🎁 Промокоды</b>\n\n"
        "Отправьте промокод сообщением — бот сразу начислит награду.\n"
        "Например: <code>KING2026</code>\n\n"
        "Промокоды можно получить у админов чата, в конкурсах и розыгрышах.",
        reply_markup=ui.back_menu(),
    )
    await callback.answer()


@router.message(Command("promo"))
async def cmd_promo(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip()
    if not code:
        await message.answer("Формат: <code>/promo ПРОМОКОД</code>")
        return
    await apply_promo(message, code)


@router.message(F.text.regexp(r"^[A-Z0-9][A-Z0-9_\-]{2,31}$"))
async def maybe_promo(message: Message) -> None:
    """Если сообщение похоже на код и такой промокод существует — активируем."""
    code = (message.text or "").strip()
    promo = await svc.get_promo(code)
    if promo is None:
        await message.answer(
            f"❌ Промокод <code>{ui.esc(code)}</code> не найден или уже отключён.\n"
            "Введите другой код или откройте /promo."
        )
        return
    await apply_promo(message, code)


async def apply_promo(message: Message, code: str) -> None:
    user = message.from_user
    await svc.ensure_user(user.id, user.username, user.first_name)
    ok, status, promo = await svc.redeem_promo(user.id, code)

    if not ok:
        reasons = {
            "not_found": "❌ Такого промокода нет или он уже отключён.",
            "expired": "⏳ Срок действия промокода истёк.",
            "already_used": "🙃 Вы уже использовали этот промокод.",
            "exhausted": "😔 Промокод уже полностью разобрали.",
            "disabled": "🚫 Промокод отключён администратором.",
            "title_missing": "⚠️ Титул из промокода больше недоступен.",
        }
        await message.answer(reasons.get(status, "❌ Не получилось активировать промокод."))
        return

    parts: list[str] = ["<b>🎉 Промокод активирован!</b>\n"]
    if "coins" in status.split(","):
        parts.append(f"💰 +{promo['coins']} монет")
    if "title" in status.split(","):
        title = await svc.get_title(promo["title_id"])
        if title:
            parts.append(f"🎭 Новый титул: {ui.title_html(title)}")
    elif "title_owned" in status.split(","):
        parts.append("🎭 Титул из промокода у вас уже был — начисляем только бонусы.")
    parts.append(f"\nМонет на счету: <b>{await balance(user.id)}</b> 🪙")
    await message.answer("\n".join(parts))


async def balance(user_id: int) -> int:
    row = await svc.get_user(user_id)
    return int(row["coins"]) if row else 0
