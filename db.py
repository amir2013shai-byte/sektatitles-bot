"""
Слой данных: SQLite через aiosqlite.
Одна общая коннекция + asyncio.Lock — просто и достаточно для одного процесса.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Iterable, Sequence

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    coins        INTEGER NOT NULL DEFAULT 0,
    equipped_id  INTEGER,
    emoji_id     INTEGER,
    last_daily   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS titles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    rarity          TEXT NOT NULL DEFAULT 'common',
    emoji           TEXT NOT NULL DEFAULT '🎖',
    custom_emoji_id TEXT,
    price           INTEGER NOT NULL DEFAULT 0,   -- 0 = нельзя купить за монеты
    stars           INTEGER NOT NULL DEFAULT 0,   -- 0 = нельзя купить за звёзды
    stock           INTEGER NOT NULL DEFAULT -1,  -- -1 = без ограничений
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inventory (
    user_id     INTEGER NOT NULL,
    title_id    INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT 'grant',
    obtained_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, title_id)
);

CREATE TABLE IF NOT EXISTS promos (
    code       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL DEFAULT 'coins',   -- coins | title | both
    coins      INTEGER NOT NULL DEFAULT 0,
    title_id   INTEGER,
    uses_left  INTEGER NOT NULL DEFAULT -1,     -- -1 = без ограничений
    per_user   INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS promo_uses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL,
    user_id    INTEGER NOT NULL,
    used_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_promo_uses ON promo_uses(code, user_id);

CREATE TABLE IF NOT EXISTS chat_members (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS emojis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    emoji           TEXT NOT NULL,
    custom_emoji_id TEXT,
    set_name        TEXT,
    rank            INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS grants_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    actor_id   INTEGER,
    target_id  INTEGER,
    title_id   INTEGER,
    mode       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_conn: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def init_db(path: str) -> aiosqlite.Connection:
    """Открывает БД, создаёт схему и наполняет каталог начальными данными."""
    global _conn, _DB_PATH
    _DB_PATH = path
    if _conn is not None:
        return _conn

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    await conn.commit()
    _conn = conn

    from catalog import seed

    await seed(conn)
    await migrate_v2()
    return conn


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def _c() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("База данных не инициализирована: вызовите init_db()")
    return _conn


async def execute(sql: str, params: Sequence[Any] = ()) -> None:
    async with _lock:
        await _c().execute(sql, params)
        await _c().commit()


async def executemany(sql: str, rows: Iterable[Sequence[Any]]) -> None:
    async with _lock:
        await _c().executemany(sql, list(rows))
        await _c().commit()


async def execute_count(sql: str, params: Sequence[Any] = ()) -> int:
    """Выполняет SQL и возвращает число изменённых строк (для атомарных условных апдейтов)."""
    async with _lock:
        cur = await _c().execute(sql, params)
        count = int(cur.rowcount)
        await _c().commit()
        return count


@asynccontextmanager
async def transaction():
    """Атомарная транзакция на общем соединении; внутри используйте conn напрямую."""
    async with _lock:
        conn = _c()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise


async def fetch_one(sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
    async with _c().execute(sql, params) as cur:
        return await cur.fetchone()


async def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
    async with _c().execute(sql, params) as cur:
        return list(await cur.fetchall())


async def scalar(sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
    row = await fetch_one(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


async def backup_to(target_path: str) -> str:
    """Создаёт консистентную копию БД (VACUUM INTO) и возвращает путь."""
    target = os.path.abspath(target_path)
    if os.path.exists(target):
        os.remove(target)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    await execute("VACUUM INTO ?", (target,))
    return target


async def restore_from(source_path: str) -> None:
    """Заменяет текущую БД файлом из source_path и переоткрывает соединение."""
    import shutil

    await close_db()
    shutil.copyfile(source_path, _DB_PATH)
    for suffix in ("-wal", "-shm"):
        candidate = _DB_PATH + suffix
        if os.path.exists(candidate):
            os.remove(candidate)
    await init_db(_DB_PATH)


_DB_PATH: str = ""


SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS counters (
    user_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS quest_state (
    user_id      INTEGER NOT NULL,
    quest_id     TEXT NOT NULL,
    progress     INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    claimed_at   TEXT,
    PRIMARY KEY (user_id, quest_id)
);

CREATE TABLE IF NOT EXISTS activity_daily (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    day      TEXT NOT NULL,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id, day)
);

CREATE TABLE IF NOT EXISTS seasons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ends_at    TEXT NOT NULL,
    closed     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS season_scores (
    season_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    points    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season_id, user_id)
);

CREATE TABLE IF NOT EXISTS rentals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id       INTEGER NOT NULL,
    owner_id       INTEGER NOT NULL,
    renter_id      INTEGER,
    price          INTEGER NOT NULL,
    duration_hours INTEGER NOT NULL,
    state          TEXT NOT NULL DEFAULT 'open',
    starts_at      TEXT,
    ends_at        TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS auctions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id    INTEGER NOT NULL,
    start_price INTEGER NOT NULL,
    top_user    INTEGER,
    top_bid     INTEGER NOT NULL DEFAULT 0,
    ends_at     TEXT NOT NULL,
    closed      INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER,
    seller_id   INTEGER
);

CREATE TABLE IF NOT EXISTS duels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    challenger  INTEGER NOT NULL,
    opponent    INTEGER NOT NULL,
    stake       INTEGER NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending',
    winner      INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    from_user  INTEGER NOT NULL,
    to_user    INTEGER NOT NULL,
    from_title INTEGER NOT NULL,
    to_title   INTEGER NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    amount     INTEGER NOT NULL DEFAULT 0,
    meta       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def migrate_v2() -> None:
    """Досоздаёт таблицы второго сезона (идемпотентно)."""
    async with _lock:
        await _c().executescript(SCHEMA_V2)
        # Мягкая миграция уже существующей таблицы аукционов.
        cols = {row[1] for row in await (await _c().execute("PRAGMA table_info(auctions)")).fetchall()}
        if "seller_id" not in cols:
            await _c().execute("ALTER TABLE auctions ADD COLUMN seller_id INTEGER")
        await _c().execute("CREATE INDEX IF NOT EXISTS idx_activity_user_day ON activity_daily(user_id, day)")
        await _c().execute("CREATE INDEX IF NOT EXISTS idx_season_scores ON season_scores(season_id, points DESC)")
        await _c().execute("CREATE INDEX IF NOT EXISTS idx_auctions_open ON auctions(closed, ends_at)")
        await _c().execute("CREATE INDEX IF NOT EXISTS idx_rentals_open ON rentals(state, ends_at)")
        await _c().commit()
