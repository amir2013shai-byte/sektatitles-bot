"""Начальные данные: каталог титулов и набор эмодзи."""
from __future__ import annotations

import aiosqlite

RARITIES = {
    "common":    {"label": "обычный",   "dot": "⚪", "stars": ""},
    "rare":      {"label": "редкий",    "dot": "🔵", "stars": "★"},
    "epic":      {"label": "эпический", "dot": "🟣", "stars": "★★"},
    "legendary": {"label": "легендарный", "dot": "🟡", "stars": "★★★"},
    "mythic":    {"label": "мифический", "dot": "🔴", "stars": "★★★★"},
}


# Эти титулы выдаются только квестами, сезонами или за активность — они не входят в магазин/лутбоксы.
REWARD_TITLE_CODES = frozenset({
    "newbie_active", "talker", "orator", "legend_active", "active_week", "active_month",
    "duelist", "collector",
    "season_gold", "season_silver", "season_bronze", "alphabet", "egoist", "owl",
    "second", "keymaster", "alchemist", "pantheon", "bankrupt",
})

# code, name, rarity, emoji, price (монеты), stars, stock
DEFAULT_TITLES = [
    ("novice",      "Новичок",         "common",    "🐣", 150,   0, -1),
    ("reader",      "Читатель",        "common",    "📖", 200,   0, -1),
    ("memer",       "Мемолог",         "common",    "😹", 250,   0, -1),
    ("nightowl",    "Полуночник",      "common",    "🌙", 400,   0, -1),
    ("gamer",       "Геймер",          "rare",      "🎮", 600,   0, -1),
    ("coder",       "Кодер",           "rare",      "💻", 900,   0, -1),
    ("philosopher", "Философ",         "rare",      "🦉", 1200,  0, -1),
    ("catlord",     "Котовладелец",    "epic",      "🐈", 2500,  0, -1),
    ("oligarch",    "Олигарх",         "epic",      "💰", 4000,  0, -1),
    ("sheriff",     "Шериф чата",      "epic",      "🤠", 6000,  0, -1),
    ("dragon",      "Дракон",          "legendary", "🐉", 9000,  0, -1),
    ("king",        "Король",          "legendary", "👑", 15000, 0, -1),
    ("ghost",       "Неоновый призрак", "epic",     "👾", 0,   100, -1),
    ("emperor",     "Император",       "legendary", "⚡", 0,   250, -1),
    ("myth",        "Легенда чата",    "mythic",    "🔥", 40000, 0, -1),
    ("founder",     "Основатель",      "mythic",    "🛡", 0,     0, 10),
    # ==== награды обновления: квесты, активность, сезоны, крафт, аукцион ====
    ("newbie_active", "Первый шаг",     "common",    "🌱", 0, 0, -1),
    ("talker",      "Болтун",          "common",    "🗣", 0, 0, -1),
    ("orator",      "Оратор",          "rare",      "🎙", 0, 0, -1),
    ("legend_active", "Живая легенда", "epic",      "🕯", 0, 0, -1),
    ("active_week", "Свой человек",    "rare",      "🤝", 0, 0, -1),
    ("active_month", "Ветеран чата",   "legendary", "🎗", 0, 0, -1),
    ("duelist",     "Дуэлянт",         "epic",      "⚔️", 0, 0, -1),
    ("collector",   "Коллекционер",    "epic",      "🗃", 0, 0, -1),
    ("season_gold", "Чемпион сезона",  "legendary", "🥇", 0, 0, 1),
    ("season_silver", "Вице-чемпион",  "epic",      "🥈", 0, 0, 1),
    ("season_bronze", "Бронзовый призёр", "rare",   "🥉", 0, 0, 1),
    # ==== секретные ====
    ("alphabet",    "Буквоед",         "mythic",    "🔤", 0, 0, -1),
    ("egoist",      "Эгоист",          "epic",      "🪞", 0, 0, -1),
    ("owl",         "Полуночный страж", "epic",     "🦉", 0, 0, -1),
    ("second",      "Вечный второй",   "rare",      "🥀", 0, 0, -1),
    ("keymaster",   "Ключник",         "legendary", "🗝", 0, 0, -1),
    ("alchemist",   "Алхимик",         "legendary", "⚗️", 0, 0, -1),
    ("pantheon",    "Пантеон",         "mythic",    "🏛", 0, 0, -1),
    ("bankrupt",    "Банкрот",         "common",    "🕳", 0, 0, -1),
]

# Базовый набор обычных эмодзи для кастомизации (без Telegram Premium)
DEFAULT_EMOJIS = [
    "🎖", "👑", "🔥", "⚡", "💎", "🖤", "🤍", "❤️", "💛", "💚",
    "🐉", "🦅", "🐺", "🦊", "🐈", "🍀", "🌟", "☄️", "🌙", "☀️",
    "😎", "🤖", "👾", "🎯", "🚀", "🏆", "🎩", "🃏", "🧿", "☠️",
]


async def seed(conn: aiosqlite.Connection) -> None:
    for code, name, rarity, emoji, price, stars, stock in DEFAULT_TITLES:
        await conn.execute(
            """INSERT OR IGNORE INTO titles
                   (code, name, rarity, emoji, price, stars, stock)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (code, name, rarity, emoji, price, stars, stock),
        )

    count = (await (await conn.execute("SELECT COUNT(*) FROM emojis")).fetchone())[0]
    if not count:
        await conn.executemany(
            "INSERT INTO emojis (emoji, custom_emoji_id, set_name, rank) VALUES (?, NULL, 'base', ?)",
            [(e, i) for i, e in enumerate(DEFAULT_EMOJIS)],
        )
    await conn.commit()
