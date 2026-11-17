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
