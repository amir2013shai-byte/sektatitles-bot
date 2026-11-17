"""
ИИ-генератор титулов и промо-кампаний.

Работает без внешних API (офлайн-генерация по ключевым словам и шаблонам), но если
задан OPENAI_API_KEY — дополнительно может придумать название через модель.

Возможности:
  • generate(описание)  → название, редкость, эмодзи, цена
  • campaign(тема)      → набор промокодов с наградами
  • emoji_for(текст)    → подбор эмодзи под тему
"""
from __future__ import annotations

import os
import random
import re

import services as svc
from catalog import RARITIES

EPIC_WORDS = [
    "легендарный", "древний", "запретный", "неоновый", "космический", "проклятый",
    "квантовый", "божественный", "теневой", "хрустальный", "огненный", "ледяной",
    "бесконечный", "забытый", "золотой", "цифровой", "лунный", "звёздный",
]
NOUNS = [
    "страж", "дракон", "скиталец", "оракул", "меценат", "трикстер", "архивариус",
    "алхимик", "картограф", "хроникёр", "механик", "инквизитор", "бард", "самурай",
    "пилигрим", "навигатор", "коллекционер", "легенда", "призрак", "титан",
]
THEMES = {
    "неон": ("👾", "epic"), "кибер": ("🤖", "epic"), "код": ("💻", "rare"),
    "кот": ("🐈", "epic"), "пёс": ("🐺", "rare"), "дракон": ("🐉", "legendary"),
    "огонь": ("🔥", "legendary"), "лёд": ("❄️", "rare"), "космос": ("🌌", "legendary"),
    "звезда": ("⭐", "epic"), "луна": ("🌙", "rare"), "ночь": ("🌑", "rare"),
    "золото": ("👑", "legendary"), "король": ("👑", "legendary"), "бог": ("⚡", "mythic"),
    "мем": ("😹", "common"), "книга": ("📖", "common"), "игра": ("🎮", "rare"),
    "музыка": ("🎧", "rare"), "еда": ("🍕", "common"), "кофе": ("☕", "common"),
    "дуэль": ("⚔️", "epic"), "магия": ("🔮", "epic"), "призрак": ("👻", "epic"),
}
PRICE_BY_RARITY = {"common": 300, "rare": 900, "epic": 2500, "legendary": 9000, "mythic": 30000}


def emoji_for(text: str) -> tuple[str, str]:
    """Возвращает (эмодзи, редкость) по ключевым словам."""
    low = text.lower()
    for key in sorted(THEMES, key=len, reverse=True):
        if key in low:
            return THEMES[key]
    return random.choice(["🎖", "🌟", "🎯", "🧿", "🃏"]), "rare"



def emoji_pack(description: str, limit: int = 8) -> list[str]:
    """Подбирает короткий тематический набор обычных Telegram-эмодзи."""
    low = (description or "").lower()
    chosen: list[str] = []
    for key in sorted(THEMES, key=len, reverse=True):
        if key in low:
            emoji = THEMES[key][0]
            if emoji not in chosen:
                chosen.append(emoji)
    extras = ["🎖", "✨", "🌟", "🧿", "🎯", "💠"]
    for emoji in extras:
        if emoji not in chosen:
            chosen.append(emoji)
    return chosen[:max(1, min(limit, 12))]

def _slug(text: str) -> str:
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    }
    out = "".join(translit.get(ch, ch) for ch in text.lower())
    out = re.sub(r"[^a-z0-9]+", "_", out).strip("_")
    return out[:24] or "title"


def generate(description: str, rarity: str | None = None) -> dict:
    """Генерирует титул по описанию: название, редкость, эмодзи, цены, код."""
    description = (description or "").strip()
    emoji, theme_rarity = emoji_for(description)
    rarity = (rarity or theme_rarity).lower()
    if rarity not in RARITIES:
        rarity = "rare"

    words = [w for w in re.split(r"\s+", description) if w]
    if description:
        base = description.strip().capitalize()
        name = base if len(base) <= 24 else base[:24].rsplit(" ", 1)[0]
    else:
        name = f"{random.choice(EPIC_WORDS).capitalize()} {random.choice(NOUNS)}"

    price = PRICE_BY_RARITY.get(rarity, 900)
    stars = {"legendary": 200, "mythic": 500}.get(rarity, 0)
    return {
        "code": _slug(name) + (f"_{random.randint(10, 99)}" if not description else ""),
        "name": name,
        "rarity": rarity,
        "emoji": emoji,
        "price": price,
        "stars": stars,
        "stock": -1,
        "theme": next((k for k in THEMES if k in description.lower()), None),
    }


def campaign(theme: str, count: int = 5, coins: int = 500) -> list[dict]:
    """Набор промокодов под тему конкурса."""
    theme = (theme or "SEKTA").strip().upper()
    base = re.sub(r"[^A-Z0-9]", "", _slug(theme).upper())[:10] or "SEKTA"
    codes = []
    for i in range(max(1, min(count, 20))):
        suffix = random.choice(["WIN", "BOOST", "GIFT", "DROP", "FIRE", "STAR", "ACE"])
        codes.append({
            "code": f"{base}{random.randint(100, 999)}{suffix}"[:32],
            "coins": coins,
            "title_code": None,
        })
    return codes


async def ai_name(description: str) -> str | None:
    """Опционально: если задан OPENAI_API_KEY — попросить модель придумать короткое название."""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return None
    try:
        import aiohttp

        payload = {
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": "Придумай короткое (до 24 символов) название титула на русском. "
                                              "Ответь только названием, без кавычек."},
                {"role": "user", "content": description},
            ],
            "max_tokens": 30,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
                timeout=20,
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()[:24]
    except Exception:
        return None


async def create_from_description(description: str, rarity: str | None = None) -> tuple[dict, int]:
    spec = generate(description, rarity)
    title_id = await svc.add_title(
        spec["code"], spec["name"], spec["rarity"], spec["emoji"],
        spec["price"], spec["stars"], spec["stock"],
    )
    spec["id"] = title_id
    return spec, title_id
