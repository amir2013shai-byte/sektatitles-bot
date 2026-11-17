"""
Экономика: казино, дуэли, крафт, лутбоксы, аукцион, обмен титулами.

Все операции идемпотентны и проверяют баланс до списания.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import db
import services as svc

UTC = timezone.utc

CASINO_MIN, CASINO_MAX = 10, 10_000
DUEL_TTL_SECONDS = 120
LOOTBOX_PRICE = 500
LOOTBOX_WEIGHTS = {"common": 55, "rare": 27, "epic": 12, "legendary": 5, "mythic": 1}
CRAFT_NEED = 3
RARITY_ORDER = ["common", "rare", "epic", "legendary", "mythic"]


async def _balance(user_id: int) -> int:
    return int(await db.scalar("SELECT coins FROM users WHERE user_id = ?", (user_id,), 0))


async def _spend(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return False
    await svc.ensure_user(user_id)
    # Одно атомарное условное UPDATE защищает от двойных ставок при параллельных апдейтах.
    changed = await db.execute_count(
        "UPDATE users SET coins = coins - ? WHERE user_id = ? AND coins >= ?",
        (amount, user_id, amount),
    )
    if not changed:
        return False
    try:
        import quests

        await quests.on_event(user_id, "coins_spent", amount)
    except Exception:
        pass
    return True


async def _win(user_id: int, amount: int) -> int:
    return await svc.add_coins(user_id, amount)


# ------------------------------------------------------------------- казино
async def casino_dice(user_id: int, stake: int) -> dict:
    """Кубик против бота: больше — победа (x2), ничья — проигрыш."""
    if stake < CASINO_MIN:
        return {"ok": False, "error": f"Минимальная ставка {CASINO_MIN} 🪙"}
    if stake > CASINO_MAX:
        return {"ok": False, "error": f"Максимальная ставка {CASINO_MAX} 🪙"}
    if not await _spend(user_id, stake):
        return {"ok": False, "error": "Недостаточно монет"}

    player, bot = random.randint(1, 6), random.randint(1, 6)
    if player > bot:
        payout = stake * 2
        await _win(user_id, payout)
        result = {"ok": True, "win": True, "player": player, "bot": bot, "delta": stake}
    else:
        result = {"ok": True, "win": False, "player": player, "bot": bot, "delta": -stake}

    await db.execute(
        "INSERT INTO event_log (user_id, kind, amount, meta) VALUES (?, 'casino', ?, ?)",
        (user_id, result["delta"], f"{player}:{bot}"),
    )
    result["balance"] = await _balance(user_id)
    return result


async def casino_coin(user_id: int, stake: int, side: str = "heads") -> dict:
    """Монетка: угадал сторону — x1.9."""
    if stake < CASINO_MIN:
        return {"ok": False, "error": f"Минимальная ставка {CASINO_MIN} 🪙"}
    if stake > CASINO_MAX:
        return {"ok": False, "error": f"Максимальная ставка {CASINO_MAX} 🪙"}
    if side not in ("heads", "tails"):
        return {"ok": False, "error": "Выберите орла или решку"}
    if not await _spend(user_id, stake):
        return {"ok": False, "error": "Недостаточно монет"}
    flip = random.choice(["heads", "tails"])
    win = flip == side
    if win:
        await _win(user_id, int(stake * 1.9))
    result = {"ok": True, "win": win, "flip": flip, "delta": int(stake * 0.9) if win else -stake}
    result["balance"] = await _balance(user_id)
    return result


# ------------------------------------------------------------------- дуэли
async def _cancel_expired_duels() -> None:
    rows = await db.fetch_all("SELECT * FROM duels WHERE state = 'pending'")
    now = datetime.now(UTC)
    for duel in rows:
        try:
            if datetime.fromisoformat(duel["expires_at"]) < now:
                await _cancel_duel(duel)
        except (TypeError, ValueError):
            await _cancel_duel(duel)


async def create_duel(chat_id: int, challenger: int, opponent: int, stake: int) -> tuple[bool, str, int]:
    await _cancel_expired_duels()
    if challenger == opponent:
        return False, "Нельзя вызвать самого себя", 0
    if stake < CASINO_MIN:
        return False, f"Минимальная ставка {CASINO_MIN} 🪙", 0
    if not await _spend(challenger, stake):
        return False, "Недостаточно монет для ставки", 0
    expires = (datetime.now(UTC) + timedelta(seconds=DUEL_TTL_SECONDS)).isoformat()
    await db.execute(
        """INSERT INTO duels (chat_id, challenger, opponent, stake, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (chat_id, challenger, opponent, stake, expires),
    )
    duel_id = int(await db.scalar("SELECT MAX(id) FROM duels"))
    return True, "ok", duel_id


async def duel_info(duel_id: int):
    return await db.fetch_one("SELECT * FROM duels WHERE id = ?", (duel_id,))


async def accept_duel(duel_id: int, user_id: int) -> dict:
    await _cancel_expired_duels()
    duel = await duel_info(duel_id)
    if duel is None:
        return {"ok": False, "error": "Дуэль не найдена"}
    if duel["state"] != "pending":
        return {"ok": False, "error": "Дуэль уже завершена"}
    if duel["opponent"] != user_id:
        return {"ok": False, "error": "Это вызов не для вас"}
    if datetime.fromisoformat(duel["expires_at"]) < datetime.now(UTC):
        await _cancel_duel(duel)
        return {"ok": False, "error": "Вызов истёк"}
    if not await _spend(user_id, duel["stake"]):
        return {"ok": False, "error": "Недостаточно монет для ставки"}
    locked = await db.execute_count(
        "UPDATE duels SET state = 'resolving' WHERE id = ? AND state = 'pending'", (duel_id,))
    if not locked:
        await _win(user_id, duel["stake"])
        return {"ok": False, "error": "Дуэль уже завершена"}

    c_roll, o_roll = random.randint(1, 6), random.randint(1, 6)
    while c_roll == o_roll:
        c_roll, o_roll = random.randint(1, 6), random.randint(1, 6)
    winner = duel["challenger"] if c_roll > o_roll else duel["opponent"]
    loser = duel["opponent"] if winner == duel["challenger"] else duel["challenger"]
    pot = duel["stake"] * 2
    await _win(winner, pot)
    await db.execute("UPDATE duels SET state = 'done', winner = ? WHERE id = ?", (winner, duel_id))

    try:
        import quests
        import activity

        await quests.on_event(winner, "duels_won", 1)
        await quests.on_event(loser, "duels_lost", 1)
        await activity.add_season_points(winner, activity.SEASON_POINTS["duel_win"])
    except Exception:
        pass

    return {
        "ok": True, "winner": winner, "loser": loser, "pot": pot,
        "challenger": duel["challenger"], "opponent": duel["opponent"],
        "c_roll": c_roll, "o_roll": o_roll,
    }


async def decline_duel(duel_id: int, user_id: int) -> bool:
    duel = await duel_info(duel_id)
    if duel is None or duel["state"] != "pending":
        return False
    if duel["opponent"] != user_id and duel["challenger"] != user_id:
        return False
    await _cancel_duel(duel)
    return True


async def _cancel_duel(duel) -> None:
    changed = await db.execute_count(
        "UPDATE duels SET state = 'cancelled' WHERE id = ? AND state = 'pending'", (duel["id"],))
    if changed:
        await _win(duel["challenger"], duel["stake"])  # возвращаем ставку только один раз


# ------------------------------------------------------------------- крафт
async def craft(user_id: int, rarity: str) -> dict:
    """3 титула одной редкости → 1 случайный титул следующей редкости."""
    rarity = rarity.lower()
    if rarity not in RARITY_ORDER[:-1]:
        return {"ok": False, "error": "Крафт возможен для common/rare/epic/legendary"}
    owned = await svc.inventory(user_id)
    same = [t for t in owned if t["rarity"] == rarity
            and not str(t["inventory_source"]).startswith("rent:")]
    if len(same) < CRAFT_NEED:
        return {"ok": False, "error": f"Нужно {CRAFT_NEED} титула редкости «{rarity}», у вас {len(same)}"}

    next_rarity = RARITY_ORDER[RARITY_ORDER.index(rarity) + 1]
    owned_ids = {t["id"] for t in owned}
    pool = [t for t in await svc.all_titles() if t["rarity"] == next_rarity and t["id"] not in owned_ids]
    if not pool:
        return {"ok": False, "error": "Нет доступного нового титула следующей редкости"}

    user = await svc.get_user(user_id)
    # не съедаем надетый титул
    victims = [t for t in same if t["id"] != (user["equipped_id"] if user else None)][:CRAFT_NEED]
    if len(victims) < CRAFT_NEED:
        return {"ok": False, "error": "Надетый титул не участвует в крафте — снимите его или добавьте ещё титулов"}

    for t in victims:
        await db.execute("DELETE FROM inventory WHERE user_id = ? AND title_id = ?", (user_id, t["id"]))

    prize = random.choice(pool)
    new = await svc.give_title(user_id, prize["id"], source="craft")
    if not new:
        # Теоретический concurrent fallback: не теряем ресурсы пользователя.
        for t in victims:
            await svc.give_title(user_id, t["id"], source="craft_refund")
        return {"ok": False, "error": "Награда уже была получена; материалы возвращены"}
    try:
        import quests

        await quests.on_event(user_id, "craft", 1)
    except Exception:
        pass
    return {
        "ok": True, "spent": [t["name"] for t in victims], "title": prize, "rarity": next_rarity,
        "duplicate": not new,
    }


# ---------------------------------------------------------------- лутбоксы
async def lootbox(user_id: int) -> dict:
    if not await _spend(user_id, LOOTBOX_PRICE):
        return {"ok": False, "error": f"Нужно {LOOTBOX_PRICE} 🪙"}
    rarities = list(LOOTBOX_WEIGHTS)
    weights = [LOOTBOX_WEIGHTS[r] for r in rarities]
    rarity = random.choices(rarities, weights=weights, k=1)[0]

    owned_ids = {t["id"] for t in await svc.inventory(user_id)}
    pool = [t for t in await svc.all_titles() if t["rarity"] == rarity]
    fresh = [t for t in pool if t["id"] not in owned_ids]
    candidates = fresh or pool
    if not candidates:
        await _win(user_id, LOOTBOX_PRICE)
        return {"ok": True, "refund": True, "rarity": rarity, "coins": LOOTBOX_PRICE}

    prize = random.choice(candidates)
    new = await svc.give_title(user_id, prize["id"], source="lootbox")
    compensation = 0 if new else max(100, prize["price"] // 4)
    if compensation:
        await _win(user_id, compensation)
    try:
        import quests

        await quests.on_event(user_id, "lootbox", 1)
    except Exception:
        pass
    return {"ok": True, "title": prize, "rarity": rarity, "duplicate": not new,
            "compensation": compensation, "balance": await _balance(user_id)}


# ------------------------------------------------------------------ аренда титулов
RENTAL_FEE_PERCENT = 5


async def expire_rentals() -> int:
    """Возвращает истёкшие титулы владельцам; повторный вызов безопасен."""
    now = datetime.now(UTC).isoformat()
    rows = await db.fetch_all("SELECT id FROM rentals WHERE state = 'active' AND ends_at <= ?", (now,))
    expired = 0
    for row in rows:
        async with db.transaction() as conn:
            rental = await (await conn.execute(
                "SELECT * FROM rentals WHERE id = ?", (row["id"],))).fetchone()
            if rental is None or rental["state"] != "active" or rental["ends_at"] > now:
                continue
            cur = await conn.execute(
                "UPDATE rentals SET state = 'expired' WHERE id = ? AND state = 'active'", (row["id"],))
            if cur.rowcount != 1:
                continue
            source = f"rent:{rental['id']}"
            await conn.execute(
                "DELETE FROM inventory WHERE user_id = ? AND title_id = ? AND source = ?",
                (rental["renter_id"], rental["title_id"], source))
            await conn.execute(
                "UPDATE users SET equipped_id = NULL WHERE user_id = ? AND equipped_id = ?",
                (rental["renter_id"], rental["title_id"]))
            await conn.execute(
                "INSERT OR IGNORE INTO inventory (user_id, title_id, source) "
                "VALUES (?, ?, 'rental_return')", (rental["owner_id"], rental["title_id"]))
            expired += 1
    return expired


async def rentals_list():
    await expire_rentals()
    return await db.fetch_all(
        """SELECT r.*, t.name AS title_name, t.emoji AS title_emoji, t.rarity AS title_rarity,
                  u.username AS owner_username, u.first_name AS owner_name
           FROM rentals r JOIN titles t ON t.id = r.title_id
           LEFT JOIN users u ON u.user_id = r.owner_id
           WHERE r.state IN ('open', 'active') ORDER BY r.id DESC LIMIT 20""")


async def rental_info(rental_id: int):
    return await db.fetch_one(
        """SELECT r.*, t.name AS title_name, t.emoji AS title_emoji, t.rarity AS title_rarity,
                  u.username AS owner_username, u.first_name AS owner_name
           FROM rentals r JOIN titles t ON t.id = r.title_id
           LEFT JOIN users u ON u.user_id = r.owner_id WHERE r.id = ?""",
        (rental_id,))


async def create_rental(owner_id: int, title_id: int, price: int,
                        duration_hours: int) -> tuple[bool, str, int]:
    """Выставляет свой титул в аренду и резервирует его."""
    if price < 1:
        return False, "Цена аренды должна быть больше нуля", 0
    if duration_hours < 1 or duration_hours > 168:
        return False, "Срок аренды: от 1 до 168 часов", 0
    await svc.ensure_user(owner_id)
    async with db.transaction() as conn:
        user = await (await conn.execute(
            "SELECT equipped_id FROM users WHERE user_id = ?", (owner_id,))).fetchone()
        if user and user["equipped_id"] == title_id:
            return False, "Сначала снимите этот титул в /profile", 0
        owned = await (await conn.execute(
            "SELECT source FROM inventory WHERE user_id = ? AND title_id = ?", (owner_id, title_id)
        )).fetchone()
        if not owned:
            return False, "Можно сдать в аренду только свой титул", 0
        if str(owned["source"]).startswith("rent:"):
            return False, "Арендованный титул нельзя пересдавать", 0
        cur = await conn.execute(
            "DELETE FROM inventory WHERE user_id = ? AND title_id = ?", (owner_id, title_id))
        if cur.rowcount != 1:
            return False, "Не удалось зарезервировать титул", 0
        cur = await conn.execute(
            """INSERT INTO rentals (title_id, owner_id, price, duration_hours)
               VALUES (?, ?, ?, ?)""", (title_id, owner_id, price, duration_hours))
        return True, "ok", int(cur.lastrowid)


async def start_rental(rental_id: int, renter_id: int) -> dict:
    """Оплачивает аренду, выдаёт титул на срок, сразу перечисляет плату владельцу."""
    await expire_rentals()
    await svc.ensure_user(renter_id)
    rental = None
    async with db.transaction() as conn:
        rental = await (await conn.execute(
            "SELECT * FROM rentals WHERE id = ?", (rental_id,))).fetchone()
        if rental is None or rental["state"] != "open":
            return {"ok": False, "error": "Предложение больше недоступно"}
        if rental["owner_id"] == renter_id:
            return {"ok": False, "error": "Нельзя арендовать собственный титул"}
        owned = await (await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?",
            (renter_id, rental["title_id"]))).fetchone()
        if owned:
            return {"ok": False, "error": "У вас уже есть этот титул"}
        renter = await (await conn.execute(
            "SELECT coins, equipped_id FROM users WHERE user_id = ?", (renter_id,))).fetchone()
        if renter is None or renter["coins"] < rental["price"]:
            have = int(renter["coins"] if renter else 0)
            return {"ok": False, "error": f"Нужно {rental['price']} 🪙, у вас {have}"}
        start_at = datetime.now(UTC)
        ends_at = start_at + timedelta(hours=rental["duration_hours"])
        cur = await conn.execute(
            "UPDATE rentals SET state = 'active', renter_id = ?, starts_at = ?, ends_at = ? "
            "WHERE id = ? AND state = 'open'",
            (renter_id, start_at.isoformat(), ends_at.isoformat(), rental_id))
        if cur.rowcount != 1:
            return {"ok": False, "error": "Предложение уже арендовали"}
        await conn.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?",
                           (rental["price"], renter_id))
        fee = rental["price"] * RENTAL_FEE_PERCENT // 100
        payout = rental["price"] - fee
        await conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?",
                           (payout, rental["owner_id"]))
        await conn.execute(
            "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, ?)",
            (renter_id, rental["title_id"], f"rent:{rental_id}"))
        if renter["equipped_id"] is None:
            await conn.execute("UPDATE users SET equipped_id = ? WHERE user_id = ?",
                               (rental["title_id"], renter_id))
        result = {"ok": True, "rental": rental, "ends_at": ends_at.isoformat(),
                  "payout": payout, "fee": fee}
    try:
        import quests

        await quests.on_event(renter_id, "coins_spent", rental["price"])
        await quests.on_event(renter_id, "rarities_owned", 1)
    except Exception:
        pass
    return result


async def cancel_rental(rental_id: int, owner_id: int) -> tuple[bool, str]:
    """Отменить своё открытое предложение без арендатора."""
    async with db.transaction() as conn:
        rental = await (await conn.execute(
            "SELECT * FROM rentals WHERE id = ?", (rental_id,))).fetchone()
        if rental is None or rental["state"] != "open":
            return False, "Предложение уже закрыто"
        if rental["owner_id"] != owner_id:
            return False, "Это не ваше предложение"
        cur = await conn.execute(
            "UPDATE rentals SET state = 'cancelled' WHERE id = ? AND state = 'open'", (rental_id,))
        if cur.rowcount != 1:
            return False, "Предложение уже недоступно"
        await conn.execute(
            "INSERT OR IGNORE INTO inventory (user_id, title_id, source) "
            "VALUES (?, ?, 'rental_return')", (owner_id, rental["title_id"]))
    return True, "ok"


async def maintenance_loop(interval_seconds: int = 60) -> None:
    """Периодически завершает истёкшие аренды и аукционы."""
    import asyncio

    while True:
        try:
            await expire_rentals()
            await settle_expired_auctions()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("maintenance sweep failed")
        await asyncio.sleep(max(15, interval_seconds))


# ------------------------------------------------------------------ аукцион
async def create_auction(seller_id: int, title_id: int, start_price: int, hours: int) -> tuple[bool, str, int]:
    """Выставляет СОБСТВЕННЫЙ титул: титул уходит в эскроу до продажи/отмены."""
    if start_price < 1:
        return False, "Стартовая цена должна быть больше нуля", 0
    if hours < 1 or hours > 168:
        return False, "Срок аукциона: от 1 до 168 часов", 0
    await svc.ensure_user(seller_id)
    ends = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
    async with db.transaction() as conn:
        user = await (await conn.execute(
            "SELECT equipped_id FROM users WHERE user_id = ?", (seller_id,))).fetchone()
        if user and user["equipped_id"] == title_id:
            return False, "Сначала снимите этот титул в /profile", 0
        owned = await (await conn.execute(
            "SELECT source FROM inventory WHERE user_id = ? AND title_id = ?", (seller_id, title_id))).fetchone()
        if not owned:
            return False, "Можно выставить только свой титул", 0
        if str(owned["source"]).startswith("rent:"):
            return False, "Арендованный титул нельзя выставить на аукцион", 0
        cur = await conn.execute(
            "DELETE FROM inventory WHERE user_id = ? AND title_id = ?", (seller_id, title_id))
        if cur.rowcount != 1:
            return False, "Не удалось зарезервировать титул", 0
        cur = await conn.execute(
            """INSERT INTO auctions (title_id, start_price, ends_at, created_by, seller_id)
               VALUES (?, ?, ?, ?, ?)""",
            (title_id, start_price, ends, seller_id, seller_id),
        )
        return True, "ok", int(cur.lastrowid)


async def settle_expired_auctions() -> None:
    now = datetime.now(UTC).isoformat()
    rows = await db.fetch_all("SELECT id FROM auctions WHERE closed = 0 AND ends_at <= ?", (now,))
    for row in rows:
        await close_auction(row["id"])


async def auctions_list(only_open: bool = True):
    await settle_expired_auctions()
    sql = """SELECT a.*, t.name AS title_name, t.emoji AS title_emoji, t.rarity AS title_rarity,
                    u.username AS seller_username, u.first_name AS seller_name
             FROM auctions a JOIN titles t ON t.id = a.title_id
             LEFT JOIN users u ON u.user_id = a.seller_id"""
    if only_open:
        sql += " WHERE a.closed = 0"
    sql += " ORDER BY a.id DESC LIMIT 10"
    return await db.fetch_all(sql)


async def auction_info(auction_id: int):
    return await db.fetch_one(
        """SELECT a.*, t.name AS title_name, t.emoji AS title_emoji, t.rarity AS title_rarity,
                  u.username AS seller_username, u.first_name AS seller_name
           FROM auctions a JOIN titles t ON t.id = a.title_id
           LEFT JOIN users u ON u.user_id = a.seller_id WHERE a.id = ?""",
        (auction_id,))


async def place_bid(auction_id: int, user_id: int, amount: int) -> dict:
    if amount <= 0:
        return {"ok": False, "error": "Ставка должна быть положительной"}
    await svc.ensure_user(user_id)
    expired = False
    auction = None
    async with db.transaction() as conn:
        auction = await (await conn.execute(
            """SELECT a.*, t.name AS title_name, t.emoji AS title_emoji,
                      t.rarity AS title_rarity, u.first_name AS seller_name
               FROM auctions a JOIN titles t ON t.id = a.title_id
               LEFT JOIN users u ON u.user_id = a.seller_id WHERE a.id = ?""",
            (auction_id,))).fetchone()
        if auction is None or auction["closed"]:
            return {"ok": False, "error": "Аукцион закрыт"}
        if datetime.fromisoformat(auction["ends_at"]) < datetime.now(UTC):
            expired = True
        else:
            if auction["seller_id"] == user_id:
                return {"ok": False, "error": "Нельзя делать ставку на собственный титул"}
            owned = await (await conn.execute(
                "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?", (user_id, auction["title_id"])
            )).fetchone()
            if owned:
                return {"ok": False, "error": "У вас уже есть этот титул"}
            minimum = max(auction["start_price"], auction["top_bid"] + 1)
            if amount < minimum:
                return {"ok": False, "error": f"Минимальная ставка: {minimum} 🪙"}
            if auction["top_user"] == user_id:
                return {"ok": False, "error": "Вы уже лидируете"}
            cur = await conn.execute(
                "UPDATE users SET coins = coins - ? WHERE user_id = ? AND coins >= ?",
                (amount, user_id, amount))
            if cur.rowcount != 1:
                return {"ok": False, "error": "Недостаточно монет"}
            if auction["top_user"] is not None and auction["top_bid"]:
                await conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?",
                                   (auction["top_bid"], auction["top_user"]))
            cur = await conn.execute(
                "UPDATE auctions SET top_user = ?, top_bid = ? WHERE id = ? AND closed = 0",
                (user_id, amount, auction_id))
            if cur.rowcount != 1:
                # Исключительный race: транзакция откатится через явную ошибку.
                raise RuntimeError("auction_changed_during_bid")
    if expired:
        await close_auction(auction_id)
        return {"ok": False, "error": "Время аукциона истекло"}
    try:
        import quests

        await quests.on_event(user_id, "coins_spent", amount)
    except Exception:
        pass
    return {"ok": True, "amount": amount, "auction": auction}


async def cancel_auction(auction_id: int, seller_id: int) -> tuple[bool, str]:
    """Отменить свой лот до первой ставки и вернуть титул."""
    async with db.transaction() as conn:
        auction = await (await conn.execute(
            "SELECT * FROM auctions WHERE id = ?", (auction_id,))).fetchone()
        if auction is None or auction["closed"]:
            return False, "Лот уже закрыт или не найден"
        if auction["seller_id"] != seller_id:
            return False, "Это не ваш лот"
        if auction["top_user"] is not None:
            return False, "После первой ставки лот нельзя отменить"
        cur = await conn.execute(
            "UPDATE auctions SET closed = 1 WHERE id = ? AND closed = 0", (auction_id,))
        if cur.rowcount != 1:
            return False, "Лот уже закрыт"
        await conn.execute(
            "INSERT OR IGNORE INTO inventory (user_id, title_id, source) VALUES (?, ?, 'auction_return')",
            (seller_id, auction["title_id"]))
    return True, "ok"


async def close_auction(auction_id: int) -> dict | None:
    """Завершает аукцион: титул и деньги передаются атомарно, лот без ставок возвращается."""
    result = None
    async with db.transaction() as conn:
        auction = await (await conn.execute(
            "SELECT * FROM auctions WHERE id = ?", (auction_id,))).fetchone()
        if auction is None or auction["closed"]:
            return None
        cur = await conn.execute(
            "UPDATE auctions SET closed = 1 WHERE id = ? AND closed = 0", (auction_id,))
        if cur.rowcount != 1:
            return None

        seller_id = auction["seller_id"]
        winner_id = auction["top_user"]
        if not winner_id:
            if seller_id is not None:
                await conn.execute(
                    "INSERT OR IGNORE INTO inventory (user_id, title_id, source) "
                    "VALUES (?, ?, 'auction_return')", (seller_id, auction["title_id"]))
            result = {"sold": False, "auction": auction, "returned": seller_id is not None}
        elif seller_id is None:
            # Лот предыдущей версии без продавца/эскроу: не раздаём чужой титул.
            await conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?",
                               (auction["top_bid"], winner_id))
            result = {"sold": False, "auction": auction, "returned": False}
        else:
            existing = await (await conn.execute(
                "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?",
                (winner_id, auction["title_id"]))).fetchone()
            if existing:
                await conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?",
                                   (auction["top_bid"], winner_id))
                await conn.execute(
                    "INSERT OR IGNORE INTO inventory (user_id, title_id, source) "
                    "VALUES (?, ?, 'auction_return')", (seller_id, auction["title_id"]))
                result = {"sold": False, "auction": auction, "returned": True, "duplicate": True}
            else:
                await conn.execute(
                    "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, 'auction')",
                    (winner_id, auction["title_id"]))
                fee = auction["top_bid"] * 5 // 100
                payout = auction["top_bid"] - fee
                await conn.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?",
                                   (payout, seller_id))
                result = {"sold": True, "auction": auction, "winner": winner_id,
                          "seller": seller_id, "fee": fee, "payout": payout}

    if result and result.get("sold"):
        try:
            import quests
            import activity

            await quests.on_event(result["winner"], "auction_won", 1)
            await quests.on_event(result["winner"], "rarities_owned", 1)
            await activity.add_season_points(result["winner"], activity.SEASON_POINTS["auction"])
        except Exception:
            pass
    return result


# ------------------------------------------------------------------- обмен
async def propose_trade(chat_id: int, from_user: int, to_user: int,
                        from_title: int, to_title: int) -> tuple[bool, str, int]:
    if from_user == to_user:
        return False, "Нельзя обменяться с собой", 0
    if from_title == to_title:
        return False, "Выберите два разных титула", 0
    owned_from = await db.fetch_one(
        "SELECT source FROM inventory WHERE user_id = ? AND title_id = ?", (from_user, from_title))
    owned_to = await db.fetch_one(
        "SELECT source FROM inventory WHERE user_id = ? AND title_id = ?", (to_user, to_title))
    if not owned_from:
        return False, "У вас нет этого титула", 0
    if not owned_to:
        return False, "У партнёра нет этого титула", 0
    if str(owned_from["source"]).startswith("rent:") or str(owned_to["source"]).startswith("rent:"):
        return False, "Арендованные титулы нельзя обменивать", 0
    if await svc.owns(from_user, to_title):
        return False, "У вас уже есть запрошенный титул", 0
    if await svc.owns(to_user, from_title):
        return False, "У партнёра уже есть предлагаемый титул", 0
    await db.execute(
        """INSERT INTO trades (chat_id, from_user, to_user, from_title, to_title)
           VALUES (?, ?, ?, ?, ?)""",
        (chat_id, from_user, to_user, from_title, to_title),
    )
    return True, "ok", int(await db.scalar("SELECT MAX(id) FROM trades"))


async def trade_info(trade_id: int):
    return await db.fetch_one("SELECT * FROM trades WHERE id = ?", (trade_id,))


async def accept_trade(trade_id: int, user_id: int) -> dict:
    async with db.transaction() as conn:
        trade = await (await conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))).fetchone()
        if trade is None:
            return {"ok": False, "error": "Обмен не найден"}
        if trade["state"] != "pending":
            return {"ok": False, "error": "Обмен уже завершён"}
        if trade["to_user"] != user_id:
            return {"ok": False, "error": "Это предложение не для вас"}
        from_owned = await (await conn.execute(
            "SELECT source FROM inventory WHERE user_id = ? AND title_id = ?",
            (trade["from_user"], trade["from_title"]))).fetchone()
        to_owned = await (await conn.execute(
            "SELECT source FROM inventory WHERE user_id = ? AND title_id = ?",
            (trade["to_user"], trade["to_title"]))).fetchone()
        if not from_owned:
            return {"ok": False, "error": "У партнёра больше нет этого титула"}
        if not to_owned:
            return {"ok": False, "error": "У вас больше нет этого титула"}
        if str(from_owned["source"]).startswith("rent:") or str(to_owned["source"]).startswith("rent:"):
            return {"ok": False, "error": "Арендованные титулы нельзя обменивать"}
        duplicate_from = await (await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?",
            (trade["from_user"], trade["to_title"]))).fetchone()
        duplicate_to = await (await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND title_id = ?",
            (trade["to_user"], trade["from_title"]))).fetchone()
        if duplicate_from or duplicate_to:
            return {"ok": False, "error": "Участник уже получил один из титулов; обмен не выполнен"}
        cur = await conn.execute(
            "UPDATE trades SET state = 'resolving' WHERE id = ? AND state = 'pending'", (trade_id,))
        if cur.rowcount != 1:
            return {"ok": False, "error": "Обмен уже принят или отменён"}
        await conn.execute("DELETE FROM inventory WHERE user_id = ? AND title_id = ?",
                           (trade["from_user"], trade["from_title"]))
        await conn.execute("DELETE FROM inventory WHERE user_id = ? AND title_id = ?",
                           (trade["to_user"], trade["to_title"]))
        await conn.execute("UPDATE users SET equipped_id = NULL WHERE user_id = ? AND equipped_id = ?",
                           (trade["from_user"], trade["from_title"]))
        await conn.execute("UPDATE users SET equipped_id = NULL WHERE user_id = ? AND equipped_id = ?",
                           (trade["to_user"], trade["to_title"]))
        await conn.execute(
            "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, 'trade')",
            (trade["to_user"], trade["from_title"]))
        await conn.execute(
            "INSERT INTO inventory (user_id, title_id, source) VALUES (?, ?, 'trade')",
            (trade["from_user"], trade["to_title"]))
        await conn.execute("UPDATE trades SET state = 'done' WHERE id = ?", (trade_id,))

    try:
        import quests

        await quests.on_event(trade["from_user"], "trade", 1)
        await quests.on_event(trade["to_user"], "trade", 1)
        await quests.on_event(trade["from_user"], "rarities_owned", 1)
        await quests.on_event(trade["to_user"], "rarities_owned", 1)
    except Exception:
        pass
    return {"ok": True, "trade": trade}


async def decline_trade(trade_id: int, user_id: int) -> bool:
    return bool(await db.execute_count(
        "UPDATE trades SET state = 'cancelled' WHERE id = ? AND state = 'pending' "
        "AND (from_user = ? OR to_user = ?)", (trade_id, user_id, user_id)))

