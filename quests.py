"""
Движок квестов.

Два типа заданий:
  • visible — видны в /quests, награду нужно забрать кнопкой;
  • secret  — НЕ видны ни в списке, ни в прогрессе. Награда выдаётся молча в момент выполнения,
              а игрок узнаёт о существовании задания только когда оно уже выполнено.

Все счётчики живут в таблице counters и обновляются через on_event().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import db
import services as svc

# ------------------------------------------------------------------ определения


@dataclass
class Quest:
    id: str
    name: str
    desc: str
    counter: str                    # какой счётчик проверяем
    target: int                     # сколько нужно
    coins: int = 0                  # награда монетами
    title_code: str | None = None   # награда титулом (код из каталога)
    secret: bool = False
    hint: str = ""                  # подсказка для секретных (не показывается в списке)

    def to_row(self) -> dict:
        return {"id": self.id, "name": self.name, "target": self.target, "coins": self.coins}


VISIBLE: list[Quest] = [
    Quest("msg_10", "Первые шаги", "Напишите 10 сообщений в чате", "messages", 10, coins=100),
    Quest("msg_100", "Болтун", "Напишите 100 сообщений", "messages", 100, coins=500, title_code="talker"),
    Quest("msg_500", "Оратор", "Напишите 500 сообщений", "messages", 500, coins=1500, title_code="orator"),
    Quest("chars_1000", "Многословный", "Напишите 1 000 символов", "chars", 1000, coins=200),
    Quest("night_20", "Ночная смена", "Напишите 20 сообщений между 00:00 и 05:00", "night", 20, coins=600),
    Quest("duel_3", "Дуэлянт", "Победите в 3 дуэлях", "duels_won", 3, coins=800, title_code="duelist"),
    Quest("craft_1", "Ремесленник", "Скрафтите 1 титул", "craft", 1, coins=600),
    Quest("loot_3", "Искатель удачи", "Откройте 3 лутбокса", "lootbox", 3, coins=700),
    Quest("titles_5", "Коллекционер", "Соберите 5 титулов", "titles_owned", 5, coins=900, title_code="collector"),
    Quest("auction_1", "Аукционист", "Выиграйте аукцион", "auction_won", 1, coins=900),
    Quest("trade_1", "Меняла", "Совершите обмен с участником", "trade", 1, coins=400),
    Quest("promo_3", "Кладоискатель", "Активируйте 3 промокода", "promo", 3, coins=800),
]

# Секретные: в списке заданий их НЕТ. Подсказки — только для админского просмотра.
SECRET: list[Quest] = [
    Quest("s_letter_a", "Буквоед", "Напишите букву «а» в 100 000 разных сообщениях",
          "letter_messages:а", 100000, coins=3000, title_code="alphabet", secret=True,
          hint="Буква «а» и очень много сообщений"),
    Quest("s_letter_ya", "Эгоист", "Напишите букву «я» в 700 разных сообщениях",
          "letter_messages:я", 700, coins=1500, title_code="egoist", secret=True,
          hint="Буква «я»"),
    Quest("s_night_50", "Сова", "50 ночных сообщений", "night", 50,
          coins=1500, title_code="owl", secret=True, hint="не спит по ночам"),
    Quest("s_duel_lose_5", "Вечный второй", "Проиграйте 5 дуэлей", "duels_lost", 5,
          coins=500, title_code="second", secret=True, hint="проиграть много дуэлей"),
    Quest("s_promo_5", "Ключник", "5 активированных промокодов", "promo", 5,
          coins=2000, title_code="keymaster", secret=True, hint="промокоды"),
    Quest("s_craft_10", "Алхимик", "10 крафтов", "craft", 10,
          coins=2500, title_code="alchemist", secret=True, hint="крафт"),
    Quest("s_all_rarity", "Пантеон", "Соберите титулы всех 5 редкостей", "rarities_owned", 5,
          coins=4000, title_code="pantheon", secret=True, hint="редкости"),
    Quest("s_bankrupt", "Банкрот", "Потратьте все монеты до нуля", "bankrupt", 1,
          coins=300, title_code="bankrupt", secret=True, hint="баланс 0"),
]

ALL: dict[str, Quest] = {q.id: q for q in VISIBLE + SECRET}

# сколько очков сезона даёт выполнение
QUEST_POINTS = 10


# ------------------------------------------------------------------- счётчики
async def add_counter(user_id: int, key: str, value: int = 1) -> int:
    if value == 0:
        return 0
    await db.execute(
        """INSERT INTO counters (user_id, key, value) VALUES (?, ?, ?)
           ON CONFLICT(user_id, key) DO UPDATE SET value = value + excluded.value""",
        (user_id, key, value),
    )
    return int(await db.scalar("SELECT value FROM counters WHERE user_id = ? AND key = ?",
                               (user_id, key)))


async def get_counter(user_id: int, key: str) -> int:
    return int(await db.scalar("SELECT value FROM counters WHERE user_id = ? AND key = ?",
                               (user_id, key)))


async def counters(user_id: int) -> dict[str, int]:
    rows = await db.fetch_all("SELECT key, value FROM counters WHERE user_id = ?", (user_id,))
    return {r["key"]: int(r["value"]) for r in rows}


# ------------------------------------------------------------------ прогресс
async def progress_of(user_id: int, quest: Quest, snap: dict[str, int] | None = None) -> int:
    if quest.counter == "titles_owned":
        return int(await db.scalar("SELECT COUNT(*) FROM inventory WHERE user_id = ?", (user_id,)))
    if quest.counter == "rarities_owned":
        return int(await db.scalar(
            """SELECT COUNT(DISTINCT t.rarity) FROM inventory i
               JOIN titles t ON t.id = i.title_id WHERE i.user_id = ?""", (user_id,)))
    data = snap if snap is not None else await counters(user_id)
    return min(data.get(quest.counter, 0), quest.target)


async def claim(user_id: int, quest_id: str) -> tuple[bool, str, Quest | None]:
    """Забрать награду видимого квеста. Возвращает (успех, причина, квест)."""
    quest = ALL.get(quest_id)
    if quest is None:
        return False, "no_quest", None
    done = await progress_of(user_id, quest)
    if done < quest.target:
        return False, "not_done", quest
    row = await db.fetch_one(
        "SELECT claimed_at FROM quest_state WHERE user_id = ? AND quest_id = ?", (user_id, quest_id)
    )
    if row and row["claimed_at"]:
        return False, "already", quest
    await db.execute(
        """INSERT INTO quest_state (user_id, quest_id, progress, completed_at, claimed_at)
           VALUES (?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(user_id, quest_id) DO UPDATE SET
               claimed_at = datetime('now'), progress = excluded.progress""",
        (user_id, quest_id, done),
    )
    await _deliver(user_id, quest)
    return True, "ok", quest


async def _deliver(user_id: int, quest: Quest) -> None:
    if quest.coins:
        await svc.add_coins(user_id, quest.coins)
    if quest.title_code:
        title = await svc.get_title_by_code(quest.title_code)
        if title:
            new = await svc.give_title(user_id, title["id"], source=f"quest:{quest.id}")
            if new:
                user = await svc.get_user(user_id)
                if user and user["equipped_id"] is None:
                    await svc.equip(user_id, title["id"])
    await db.execute(
        "INSERT INTO event_log (user_id, kind, amount, meta) VALUES (?, 'quest', ?, ?)",
        (user_id, quest.coins, quest.id),
    )
    try:
        from activity import add_season_points

        await add_season_points(user_id, QUEST_POINTS)
    except Exception:
        pass


# --------------------------------------------------------- обработка событий
async def _complete_secrets(user_id: int, changed: set[str] | None = None) -> list[Quest]:
    """Автоматически выдаёт впервые выполненные секретные квесты."""
    finished: list[Quest] = []
    snap = await counters(user_id)
    claimed_rows = await db.fetch_all(
        "SELECT quest_id FROM quest_state WHERE user_id = ? AND claimed_at IS NOT NULL", (user_id,))
    claimed = {row["quest_id"] for row in claimed_rows}
    for quest in SECRET:
        if changed is not None and quest.counter not in changed:
            continue
        if quest.id in claimed:
            continue
        if await progress_of(user_id, quest, snap) >= quest.target:
            await db.execute(
                """INSERT INTO quest_state (user_id, quest_id, progress, completed_at, claimed_at)
                   VALUES (?, ?, ?, datetime('now'), datetime('now'))
                   ON CONFLICT(user_id, quest_id) DO UPDATE SET
                       completed_at = datetime('now'), claimed_at = datetime('now')""",
                (user_id, quest.id, quest.target),
            )
            claimed.add(quest.id)
            await _deliver(user_id, quest)
            finished.append(quest)
    return finished


async def on_event(user_id: int, kind: str, value: int = 1) -> list[Quest]:
    """Учитывает событие и возвращает секретные квесты, выполненные этим событием."""
    if value <= 0:
        return []
    await add_counter(user_id, kind, value)
    changed = {kind}
    if kind == "coins_spent":
        balance = int(await db.scalar("SELECT coins FROM users WHERE user_id = ?", (user_id,), 0))
        if balance == 0:
            await add_counter(user_id, "bankrupt", 1)
            changed.add("bankrupt")
    return await _complete_secrets(user_id, changed)


async def record_message(user_id: int, text: str, night: bool = False) -> list[Quest]:
    """Считает одно реальное сообщение. Буквенные секреты учитывают разные сообщения, не символы."""
    low = text.casefold()
    increments: dict[str, int] = {"messages": 1, "chars": len(text)}
    changed: set[str] = set()
    for letter in ("а", "я"):
        count = low.count(letter)
        if count:
            increments[f"letter:{letter}"] = count
            increments[f"letter_messages:{letter}"] = 1
            changed.add(f"letter_messages:{letter}")
    if night:
        increments["night"] = 1
        changed.add("night")
    await db.executemany(
        """INSERT INTO counters (user_id, key, value) VALUES (?, ?, ?)
           ON CONFLICT(user_id, key) DO UPDATE SET value = value + excluded.value""",
        [(user_id, key, amount) for key, amount in increments.items()],
    )
    return await _complete_secrets(user_id, changed) if changed else []


async def _is_claimed(user_id: int, quest_id: str) -> bool:
    row = await db.fetch_one(
        "SELECT claimed_at FROM quest_state WHERE user_id = ? AND quest_id = ?", (user_id, quest_id)
    )
    return bool(row and row["claimed_at"])


async def visible_progress(user_id: int) -> list[tuple[Quest, int, bool]]:
    """[(квест, прогресс, забрано?)] — только видимые, для меню /quests."""
    snap = await counters(user_id)
    claimed = {
        r["quest_id"] for r in await db.fetch_all(
            "SELECT quest_id FROM quest_state WHERE user_id = ? AND claimed_at IS NOT NULL",
            (user_id,),
        )
    }
    out = []
    for quest in VISIBLE:
        progress = await progress_of(user_id, quest, snap)
        out.append((quest, progress, quest.id in claimed))
    return out


async def secret_stats(user_id: int) -> tuple[int, int]:
    """(сколько секретных выполнено, сколько всего) — для админки и профиля."""
    secret_ids = [q.id for q in SECRET]
    placeholders = ",".join("?" for _ in secret_ids)
    done = int(await db.scalar(
        f"SELECT COUNT(*) FROM quest_state WHERE user_id = ? AND quest_id IN ({placeholders}) "
        "AND claimed_at IS NOT NULL", (user_id, *secret_ids)
    ))
    return done, len(SECRET)
