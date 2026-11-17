"""Бизнес-логика: пользователи, титулы, магазин, промокоды, массовая выдача."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

import db
from config import config

UTC = timezone.utc


# ---------------------------------------------------------------- пользователи
async def ensure_user(user_id: int, username: str | None = None, first_name: str | None = None) -> aiosqlite.Row:
    await db.execute(
        """INSERT INTO users (user_id, username, first_name, coins)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               username = COALESCE(excluded.username, users.username),
               first_name = COALESCE(excluded.first_name, users.first_name)""",
        (user_id, username, first_name, config.start_coins),
    )
    row = await db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    assert row is not None
    return row


async def get_user(user_id: int) -> aiosqlite.Row | None:
    return await db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))


async def touch_chat(chat_id: int, user_id: int) -> None:
    if chat_id >= 0:  # личка и группы «малого» формата не нужны
        return
    await db.execute(
        """INSERT INTO chat_members (chat_id, user_id, last_seen)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(chat_id, user_id) DO UPDATE SET last_seen = datetime('now')""",
        (chat_id, user_id),
    )


async def add_coins(user_id: int, amount: int) -> int:
    await ensure_user(user_id)
    await db.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    return int(await db.scalar("SELECT coins FROM users WHERE user_id = ?", (user_id,)))


async def daily_bonus(user_id: int) -> tuple[bool, int]:
    row = await db.fetch_one("SELECT last_daily FROM users WHERE user_id = ?", (user_id,))
    now = datetime.now(UTC)
    if row and row["last_daily"]:
        last = datetime.fromisoformat(row["last_daily"])
        if now - last < timedelta(days=1):
            left = timedelta(days=1) - (now - last)
            return False, int(left.total_seconds())
    await db.execute(
        "UPDATE users SET last_daily = ?, coins = coins + ? WHERE user_id = ?",
        (now.isoformat(), config.daily_bonus, user_id),
    )
    return True, config.daily_bonus


# -------------------------------------------------------------------- титулы
async def all_titles(only_active: bool = True, include_rewards: bool = False) -> list[aiosqlite.Row]:
    """Каталог для магазина. Наградные титулы скрыты, если не запрошены явно."""
    from catalog import REWARD_TITLE_CODES

    clauses: list[str] = []
    params: list[Any] = []
    if only_active:
        clauses.append("active = 1")
    if not include_rewards and REWARD_TITLE_CODES:
        clauses.append("code NOT IN (" + ",".join("?" for _ in REWARD_TITLE_CODES) + ")")
        params.extend(sorted(REWARD_TITLE_CODES))
    sql = "SELECT * FROM titles"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY price = 0, price, stars, id"
    return await db.fetch_all(sql, params)


async def get_title(title_id: int) -> aiosqlite.Row | None:
    return await db.fetch_one("SELECT * FROM titles WHERE id = ?", (title_id,))


async def get_title_by_code(code: str) -> aiosqlite.Row | None:
    return await db.fetch_one("SELECT * FROM titles WHERE code = ? COLLATE NOCASE", (code,))


async def inventory(user_id: int) -> list[aiosqlite.Row]:
    return await db.fetch_all(
        """SELECT t.*, i.source AS inventory_source, i.obtained_at
           FROM inventory i
           JOIN titles t ON t.id = i.title_id
           WHERE i.user_id = ?
           ORDER BY t.id""",
        (user_id,),
    )


async def owns(user_id: int, title_id: int) -> bool:
    return bool(await db.scalar(
        "SELECT COUNT(*) FROM inventory WHERE user_id = ? AND title_id = ?", (user_id, title_id)
    ))


async def give_title(user_id: int, title_id: int, source: str = "grant") -> bool:
    """Выдать титул. True — если титула у пользователя ещё не было."""
    if await owns(user_id, title_id):
        return False
    await db.execute(
        "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, ?)",
        (user_id, title_id, source),
    )
    # Коллекционный секрет проверяется при каждом новом экземпляре.
    try:
        import quests

        await quests.on_event(user_id, "rarities_owned", 1)
    except Exception:
        pass
    return True


async def equip(user_id: int, title_id: int | None) -> aiosqlite.Row | None:
    """Надеть титул (None — снять). Возвращает строку титула или None."""
    if title_id is None:
        await db.execute("UPDATE users SET equipped_id = NULL WHERE user_id = ?", (user_id,))
        return None
    if not await owns(user_id, title_id):
        raise PermissionError("title_not_owned")
    await db.execute("UPDATE users SET equipped_id = ? WHERE user_id = ?", (title_id, user_id))
    return await get_title(title_id)


async def buy_with_coins(user_id: int, title_id: int) -> tuple[bool, str]:
    """Покупка титула за монеты: списание, тираж и выдача атомарны."""
    await ensure_user(user_id)
    async with db.transaction() as conn:
        title = await (await conn.execute(
            "SELECT * FROM titles WHERE id = ?", (title_id,))).fetchone()
        if title is None or not title["active"]:
            return False, "Титул недоступен."
        if title["price"] <= 0:
            return False, "Этот титул нельзя купить за монеты."
        owned = await (await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?", (user_id, title_id))).fetchone()
        if owned:
            return False, "У вас уже есть этот титул."
        if title["stock"] == 0:
            return False, "Тираж закончился."
        user = await (await conn.execute(
            "SELECT coins FROM users WHERE user_id = ?", (user_id,))).fetchone()
        have = int(user["coins"] if user else 0)
        if user is None or have < title["price"]:
            return False, f"Не хватает монет: нужно {title['price']}, у вас {have}."
        if title["stock"] > 0:
            cur = await conn.execute(
                "UPDATE titles SET stock = stock - 1 WHERE id = ? AND stock > 0", (title_id,))
            if cur.rowcount != 1:
                return False, "Тираж закончился."
        await conn.execute("UPDATE users SET coins = coins - ?, equipped_id = ? WHERE user_id = ?",
                           (title["price"], title_id, user_id))
        await conn.execute(
            "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, 'shop')",
            (user_id, title_id))
    try:
        import quests

        await quests.on_event(user_id, "coins_spent", int(title["price"]))
        await quests.on_event(user_id, "rarities_owned", 1)
    except Exception:
        pass
    return True, "ok"


async def deliver_stars_purchase(user_id: int, title_id: int) -> tuple[bool, str]:
    """Выдача после оплаты звёздами Telegram."""
    title = await get_title(title_id)
    if title is None:
        return False, "Титул не найден."
    if title["stock"] == 0:
        return False, "Тираж закончился."
    if title["stock"] > 0:
        await db.execute("UPDATE titles SET stock = stock - 1 WHERE id = ? AND stock > 0", (title_id,))
    new = await give_title(user_id, title_id, source="stars")
    if new:
        await equip(user_id, title_id)
    return True, "already_owned" if not new else "ok"


# --------------------------------------------------------------------- эмодзи
async def emoji_list() -> list[aiosqlite.Row]:
    return await db.fetch_all("SELECT * FROM emojis ORDER BY rank, id")


async def get_emoji(emoji_id: int) -> aiosqlite.Row | None:
    return await db.fetch_one("SELECT * FROM emojis WHERE id = ?", (emoji_id,))


async def set_emoji(user_id: int, emoji_id: int | None) -> None:
    await db.execute("UPDATE users SET emoji_id = ? WHERE user_id = ?", (emoji_id, user_id))


# ------------------------------------------------------------------ промокоды
async def create_promo(
    code: str,
    *,
    kind: str = "coins",
    coins: int = 0,
    title_id: int | None = None,
    uses_left: int = -1,
    per_user: int = 1,
    expires_at: str | None = None,
    created_by: int | None = None,
) -> None:
    await db.execute(
        """INSERT INTO promos (code, kind, coins, title_id, uses_left, per_user, expires_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
               kind = excluded.kind, coins = excluded.coins, title_id = excluded.title_id,
               uses_left = excluded.uses_left, per_user = excluded.per_user,
               expires_at = excluded.expires_at, active = 1""",
        (code.upper(), kind, coins, title_id, uses_left, per_user, expires_at, created_by),
    )


async def get_promo(code: str) -> aiosqlite.Row | None:
    return await db.fetch_one("SELECT * FROM promos WHERE code = ? COLLATE NOCASE", (code.upper(),))


async def promos_list() -> list[aiosqlite.Row]:
    return await db.fetch_all("SELECT * FROM promos ORDER BY created_at DESC LIMIT 50")


async def deactivate_promo(code: str) -> bool:
    promo = await get_promo(code)
    if promo is None:
        return False
    await db.execute("UPDATE promos SET active = 0 WHERE code = ?", (promo["code"],))
    return True


async def redeem_promo(user_id: int, code: str) -> tuple[bool, str, aiosqlite.Row | None]:
    """Атомарно активирует промокод; лимит и награды не могут задвоиться."""
    code = code.strip().upper()
    await ensure_user(user_id)
    title_was_given = False
    async with db.transaction() as conn:
        promo = await (await conn.execute(
            "SELECT * FROM promos WHERE code = ? COLLATE NOCASE", (code,))).fetchone()
        if promo is None:
            return False, "not_found", None
        if not promo["active"]:
            return False, "exhausted" if promo["uses_left"] == 0 else "disabled", promo
        if promo["expires_at"]:
            try:
                expiry = datetime.fromisoformat(promo["expires_at"])
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if datetime.now(UTC) > expiry:
                    return False, "expired", promo
            except ValueError:
                return False, "expired", promo
        used = await (await conn.execute(
            "SELECT COUNT(*) FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id)
        )).fetchone()
        if int(used[0]) >= promo["per_user"]:
            return False, "already_used", promo
        if promo["uses_left"] == 0:
            return False, "exhausted", promo

        title = None
        if promo["title_id"]:
            title = await (await conn.execute(
                "SELECT * FROM titles WHERE id = ?", (promo["title_id"],))).fetchone()
        if promo["kind"] in ("title", "both") and title is None:
            return False, "title_missing", promo

        gains: list[str] = []
        if promo["kind"] in ("coins", "both") and promo["coins"]:
            await conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?",
                               (promo["coins"], user_id))
            gains.append("coins")
        if promo["kind"] in ("title", "both") and title is not None:
            owned = await (await conn.execute(
                "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?",
                (user_id, title["id"]))).fetchone()
            if owned:
                gains.append("title_owned")
            else:
                await conn.execute(
                    "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, ?)",
                    (user_id, title["id"], f"promo:{code}"))
                user = await (await conn.execute(
                    "SELECT equipped_id FROM users WHERE user_id = ?", (user_id,))).fetchone()
                if user and user["equipped_id"] is None:
                    await conn.execute("UPDATE users SET equipped_id = ? WHERE user_id = ?",
                                       (title["id"], user_id))
                title_was_given = True
                gains.append("title")

        await conn.execute("INSERT INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id))
        if promo["uses_left"] > 0:
            await conn.execute("UPDATE promos SET uses_left = uses_left - 1 WHERE code = ?", (code,))
            left = int((await (await conn.execute(
                "SELECT uses_left FROM promos WHERE code = ?", (code,))).fetchone())[0])
            if left <= 0:
                await conn.execute("UPDATE promos SET active = 0 WHERE code = ?", (code,))
        result = (True, ",".join(gains) or "nothing", promo)

    try:
        import quests

        await quests.on_event(user_id, "promo", 1)
        if title_was_given:
            await quests.on_event(user_id, "rarities_owned", 1)
    except Exception:
        pass
    return result


# ------------------------------------------------------------ массовая выдача
async def bulk_targets(chat_id: int, mode: str) -> list[int]:
    """Кому выдавать массово.

    mode = chat    — все, кого бот видел в этом чате
         = users   — все пользователи бота (все чаты)
         = admins  — администраторы чата (заполняется в хендлере)
         = buyers  — те, у кого уже есть купленные титулы
    """
    if mode == "chat":
        rows = await db.fetch_all("SELECT user_id FROM chat_members WHERE chat_id = ?", (chat_id,))
    elif mode == "users":
        rows = await db.fetch_all("SELECT user_id FROM users")
    elif mode == "buyers":
        rows = await db.fetch_all(
            "SELECT DISTINCT user_id FROM inventory WHERE source = 'shop'"
        )
    else:
        rows = []
    return [int(r["user_id"]) for r in rows]


async def bulk_give(
    user_ids: Iterable[int],
    title_id: int,
    *,
    chat_id: int | None = None,
    actor_id: int | None = None,
    source: str = "mass",
    equip_first: bool = True,
) -> tuple[int, int]:
    """Массово выдать титул. Возвращает (выдано новым, всего обработано)."""
    given = 0
    total = 0
    for uid in user_ids:
        total += 1
        await ensure_user(uid)
        if await give_title(uid, title_id, source=source):
            given += 1
            if equip_first:
                user = await get_user(uid)
                if user and user["equipped_id"] is None:
                    await equip(uid, title_id)
        await db.execute(
            "INSERT INTO grants_log (chat_id, actor_id, target_id, title_id, mode) VALUES (?, ?, ?, ?, ?)",
            (chat_id, actor_id, uid, title_id, source),
        )
    return given, total


# ---------------------------------------------------------------- статистика
async def stats() -> dict[str, Any]:
    return {
        "users": int(await db.scalar("SELECT COUNT(*) FROM users")),
        "titles": int(await db.scalar("SELECT COUNT(*) FROM titles WHERE active = 1")),
        "issued": int(await db.scalar("SELECT COUNT(*) FROM inventory")),
        "promos": int(await db.scalar("SELECT COUNT(*) FROM promos WHERE active = 1")),
    }


async def top_by_coins(limit: int = 10) -> list[aiosqlite.Row]:
    return await db.fetch_all(
        "SELECT * FROM users ORDER BY coins DESC LIMIT ?", (limit,)
    )


async def top_by_titles(limit: int = 10) -> list[aiosqlite.Row]:
    return await db.fetch_all(
        """SELECT u.user_id, u.username, u.first_name, COUNT(i.title_id) AS cnt
           FROM users u LEFT JOIN inventory i ON i.user_id = u.user_id
           GROUP BY u.user_id ORDER BY cnt DESC, u.user_id LIMIT ?""",
        (limit,),
    )


async def revoke_title(user_id: int, title_id: int) -> bool:
    """Забрать титул у пользователя."""
    if not await owns(user_id, title_id):
        return False
    await db.execute("DELETE FROM inventory WHERE user_id = ? AND title_id = ?", (user_id, title_id))
    await db.execute(
        "UPDATE users SET equipped_id = NULL WHERE user_id = ? AND equipped_id = ?", (user_id, title_id)
    )
    return True


async def resolve_username(username: str) -> int | None:
    uname = username.lstrip("@").lower()
    row = await db.fetch_one("SELECT user_id FROM users WHERE lower(username) = ?", (uname,))
    return int(row["user_id"]) if row else None


async def add_emoji_set(set_name: str, items: list[tuple[str, str | None]]) -> int:
    """Импорт эмодзи (обычный эмодзи, custom_emoji_id) из набора Telegram."""
    added = 0
    for idx, (emoji, cid) in enumerate(items):
        if cid is None:
            continue
        exists = await db.fetch_one(
            "SELECT id FROM emojis WHERE custom_emoji_id = ?", (cid,)
        )
        if exists:
            continue
        await db.execute(
            "INSERT INTO emojis (emoji, custom_emoji_id, set_name, rank) VALUES (?, ?, ?, ?)",
            (emoji, cid, set_name, 200 + idx),
        )
        added += 1
    return added


async def add_plain_emoji_set(set_name: str, items: list[str]) -> int:
    """Добавляет обычные эмодзи в набор кастомизации, не создавая дубликаты."""
    added = 0
    for idx, emoji in enumerate(dict.fromkeys(items)):
        exists = await db.fetch_one(
            "SELECT id FROM emojis WHERE emoji = ? AND custom_emoji_id IS NULL", (emoji,))
        if exists:
            continue
        await db.execute(
            "INSERT INTO emojis (emoji, custom_emoji_id, set_name, rank) VALUES (?, NULL, ?, ?)",
            (emoji, set_name, 300 + idx),
        )
        added += 1
    return added


async def add_title(
    code: str, name: str, rarity: str, emoji: str,
    price: int = 0, stars: int = 0, stock: int = -1,
) -> int:
    await db.execute(
        """INSERT INTO titles (code, name, rarity, emoji, price, stars, stock)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
               name = excluded.name, rarity = excluded.rarity, emoji = excluded.emoji,
               price = excluded.price, stars = excluded.stars, stock = excluded.stock,
               active = 1""",
        (code, name, rarity, emoji, price, stars, stock),
    )
    row = await get_title_by_code(code)
    return int(row["id"])
