"""
Активность, автонаграды за активность и сезоны.

• считает сообщения по дням (таблица activity_daily);
• выдаёт титулы автоматически по порогам активности;
• ведёт очки сезона и подводит итоги (топ-3 получают титул и монеты).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html

import db
import services as svc

UTC = timezone.utc

# порог сообщений → код титула
AUTO_TITLES: list[tuple[int, str]] = [
    (10, "newbie_active"),
    (100, "talker"),
    (500, "orator"),
    (2000, "legend_active"),
]
ACTIVE_DAY_TITLES: list[tuple[int, str]] = [
    (7, "active_week"),
    (30, "active_month"),
]

SEASON_POINTS = {
    "message": 1,      # за сообщение (с суточным капом)
    "duel_win": 5,
    "quest": 10,
    "auction": 15,
}
DAILY_MESSAGE_CAP = 40   # больше 40 сообщений в день очков не дают


async def bump_activity(chat_id: int, user_id: int) -> int:
    """+1 сообщение за сегодня; возвращает счётчик за сегодня."""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    await db.execute(
        """INSERT INTO activity_daily (chat_id, user_id, day, messages) VALUES (?, ?, ?, 1)
           ON CONFLICT(chat_id, user_id, day) DO UPDATE SET messages = messages + 1""",
        (chat_id, user_id, day),
    )
    # Суточный лимит очков общий между чатами.
    return int(await db.scalar(
        "SELECT COALESCE(SUM(messages), 0) FROM activity_daily WHERE user_id = ? AND day = ?",
        (user_id, day)))


async def total_messages(user_id: int) -> int:
    return int(await db.scalar(
        "SELECT COALESCE(SUM(messages), 0) FROM activity_daily WHERE user_id = ?", (user_id,)))


async def check_auto_titles(user_id: int) -> list:
    """Выдаёт титулы за активность. Возвращает список новых титулов."""
    total = await total_messages(user_id)
    given = []
    for threshold, code in AUTO_TITLES:
        if total < threshold:
            continue
        title = await svc.get_title_by_code(code)
        if title is None:
            continue
        if await svc.give_title(user_id, title["id"], source=f"activity:messages:{threshold}"):
            user = await svc.get_user(user_id)
            if user and user["equipped_id"] is None:
                await svc.equip(user_id, title["id"])
            given.append(title)

    days = int(await db.scalar(
        "SELECT COUNT(DISTINCT day) FROM activity_daily WHERE user_id = ?", (user_id,), 0))
    for threshold, code in ACTIVE_DAY_TITLES:
        if days < threshold:
            continue
        title = await svc.get_title_by_code(code)
        if title is None:
            continue
        if await svc.give_title(user_id, title["id"], source=f"activity:days:{threshold}"):
            user = await svc.get_user(user_id)
            if user and user["equipped_id"] is None:
                await svc.equip(user_id, title["id"])
            given.append(title)
    return given


# ------------------------------------------------------------------- сезоны
async def active_season():
    return await db.fetch_one(
        "SELECT * FROM seasons WHERE closed = 0 ORDER BY id DESC LIMIT 1")


async def start_season(name: str, days: int = 30) -> int:
    # Не теряем награды, если владелец запустил новый сезон до ручного завершения старого.
    if await active_season() is not None:
        await close_season()
    ends = (datetime.now(UTC) + timedelta(days=days)).isoformat()
    await db.execute("INSERT INTO seasons (name, ends_at) VALUES (?, ?)", (name, ends))
    return int(await db.scalar("SELECT MAX(id) FROM seasons"))


async def add_season_points(user_id: int, points: int) -> None:
    season = await active_season()
    if season is None or points <= 0:
        return
    try:
        if datetime.fromisoformat(season["ends_at"]) <= datetime.now(UTC):
            await close_season()
            return
    except (TypeError, ValueError):
        pass
    await db.execute(
        """INSERT INTO season_scores (season_id, user_id, points) VALUES (?, ?, ?)
           ON CONFLICT(season_id, user_id) DO UPDATE SET points = points + excluded.points""",
        (season["id"], user_id, points),
    )


async def season_top(limit: int = 10):
    season = await active_season()
    if season is None:
        return None, []
    try:
        if datetime.fromisoformat(season["ends_at"]) <= datetime.now(UTC):
            await close_season()
            return None, []
    except (TypeError, ValueError):
        pass
    rows = await db.fetch_all(
        """SELECT s.user_id, s.points, u.username, u.first_name
           FROM season_scores s LEFT JOIN users u ON u.user_id = s.user_id
           WHERE s.season_id = ? ORDER BY s.points DESC, s.user_id LIMIT ?""",
        (season["id"], limit),
    )
    return season, rows


async def my_season_points(user_id: int) -> int:
    season = await active_season()
    if season is None:
        return 0
    return int(await db.scalar(
        "SELECT points FROM season_scores WHERE season_id = ? AND user_id = ?",
        (season["id"], user_id), 0))


async def close_season() -> tuple[int, list[dict]]:
    """Закрывает сезон и награждает топ-3 ровно один раз."""
    season = await active_season()
    if season is None:
        return 0, []
    rows = await db.fetch_all(
        """SELECT s.user_id, s.points, u.username, u.first_name
           FROM season_scores s LEFT JOIN users u ON u.user_id = s.user_id
           WHERE s.season_id = ? ORDER BY s.points DESC, s.user_id LIMIT 3""",
        (season["id"],),
    )
    # Закрываем до выдачи наград: триггеры новых титулов не должны добавить очки в завершённый сезон.
    changed = await db.execute_count(
        "UPDATE seasons SET closed = 1 WHERE id = ? AND closed = 0", (season["id"],))
    if not changed:
        return int(season["id"]), []
    rewards = [(5000, "season_gold"), (2500, "season_silver"), (1000, "season_bronze")]
    winners: list[dict] = []
    for idx, row in enumerate(rows):
        coins, code = rewards[min(idx, len(rewards) - 1)]
        await svc.ensure_user(row["user_id"])
        await svc.add_coins(row["user_id"], coins)
        title = await svc.get_title_by_code(code)
        if title:
            await svc.give_title(row["user_id"], title["id"], source="season")
        winners.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "name": row["first_name"],
            "points": row["points"],
            "coins": coins,
            "title": title["name"] if title else None,
        })
    return int(season["id"]), winners


async def season_leaderboard_text() -> str:
    season, rows = await season_top(10)
    if season is None:
        return "<b>🏆 Сезон</b>\n\nСейчас сезон не идёт. Админ может запустить его: /season start"
    medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 7
    lines = [f"<b>🏆 Сезон «{season['name']}»</b>",
             f"<i>до {season['ends_at'][:16].replace('T', ' ')} UTC</i>\n"]
    if not rows:
        lines.append("Пока никто не набрал очки. Сообщения, дуэли и квесты дают очки сезона.")
    for i, row in enumerate(rows):
        name = html.escape(row["first_name"] or row["username"] or str(row["user_id"]))
        lines.append(f"{medals[i]} {name} — <b>{row['points']}</b> очков")
    lines.append("\n<i>Очки: сообщение +1 (до 40 в день), победа в дуэли +5, квест +10, аукцион +15.</i>")
    return "\n".join(lines)
