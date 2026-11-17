"""
Конфигурация бота. Все значения читаются из переменных окружения (.env).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _ints(raw: str | None) -> list[int]:
    out: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


@dataclass
class Config:
    token: str = os.getenv("BOT_TOKEN", "")
    # Владельцы бота (полный доступ): ID через запятую
    owner_ids: list[int] = field(default_factory=lambda: _ints(os.getenv("OWNER_IDS")))
    # Дополнительная страховка: владелец определяется и по юзернейму (если ID не сработал)
    owner_usernames: list[str] = field(default_factory=lambda: [
        u.strip().lstrip("@").lower()
        for u in (os.getenv("OWNER_USERNAMES", "an7m3_t4n0ch7a") or "").split(",")
        if u.strip()
    ])
    # Могут ли админы чата создавать промокоды (по умолчанию — только владелец)
    allow_chat_admin_promos: bool = os.getenv("ALLOW_CHAT_ADMIN_PROMOS", "0") == "1"
    db_path: str = os.getenv("DATABASE_PATH", "data/titlebot.sqlite3")

    # Webhook-режим включается, если известен публичный адрес сервиса.
    # Приоритет: PUBLIC_BASE_URL → RENDER_EXTERNAL_URL (Render) → RAILWAY_PUBLIC_DOMAIN (Railway)
    public_base_url: str = (
        os.getenv("PUBLIC_BASE_URL", "")
        or os.getenv("RENDER_EXTERNAL_URL", "")
        or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    ).rstrip("/")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "change-me")
    webhook_path: str = os.getenv("WEBHOOK_PATH", "/telegram")
    port: int = int(os.getenv("PORT", "8080"))

    # Экономика
    daily_bonus: int = int(os.getenv("DAILY_BONUS", "75"))
    start_coins: int = int(os.getenv("START_COINS", "50"))

    # Платежи звёздами Telegram
    payments_enabled: bool = os.getenv("PAYMENTS_ENABLED", "1") == "1"
    stars_provider_token: str = os.getenv("STARS_PROVIDER_TOKEN", "")  # для XTR не нужен

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # Адрес витрины коллекции (GitHub Pages): https://USER.github.io/REPO/webapp
    webapp_url: str = os.getenv("WEBAPP_URL", "")

    def __post_init__(self) -> None:
        # Позволяем указывать домен без схемы (как отдаёт Render): name.onrender.com
        if self.public_base_url and not self.public_base_url.startswith("http"):
            self.public_base_url = "https://" + self.public_base_url
        # Панели вида FadeHost/bothost дают постоянную папку /data: если DATABASE_PATH
        # не задан явно, но папка /data существует — храним базу там (переживает деплои).
        if not os.getenv("DATABASE_PATH") and os.path.isdir("/data"):
            self.db_path = "/data/titlebot.sqlite3"

    @property
    def use_webhook(self) -> bool:
        return bool(self.public_base_url)

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    def is_owner_username(self, username: str | None) -> bool:
        return bool(username) and username.lstrip("@").lower() in self.owner_usernames

    def is_owner_user(self, user) -> bool:
        """Владелец по ID или по юзернейму (страховка от неверного OWNER_IDS)."""
        if user is None:
            return False
        return self.is_owner(getattr(user, "id", 0)) or self.is_owner_username(getattr(user, "username", None))


config = Config()
