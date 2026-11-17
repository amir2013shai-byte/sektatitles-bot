"""Тесты бизнес-логики без Telegram: магазин, промокоды, массовая выдача, эмодзи."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import services as svc  # noqa: E402

PASSED = 0


def check(cond: bool, label: str) -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASSED += 1
    print(f"  ✓ {label}")


async def main() -> None:
    tmp = tempfile.mkdtemp()
    await db.init_db(os.path.join(tmp, "test.sqlite3"))

    print("Пользователи и бонус")
    u = await svc.ensure_user(111, "alice", "Alice")
    check(u["coins"] == 50, "стартовый баланс 50 монет")
    ok, amount = await svc.daily_bonus(111)
    check(ok and amount == 75, "бонус дня начислен")
    ok2, _ = await svc.daily_bonus(111)
    check(not ok2, "повторный бонус дня заблокирован")

    print("Магазин")
    await svc.add_coins(111, 1000)  # 125 + 1000 = 1125
    novice = await svc.get_title_by_code("novice")
    ok, reason = await svc.buy_with_coins(111, novice["id"])
    check(ok, f"покупка титула за монеты ({reason})")
    check(await svc.owns(111, novice["id"]), "титул в инвентаре")
    check((await svc.get_user(111))["equipped_id"] == novice["id"], "купленный титул сразу надет")
    check(await db.scalar("SELECT coins FROM users WHERE user_id=111") == 975, "монеты списаны корректно")
    ok, reason = await svc.buy_with_coins(111, novice["id"])
    check(not ok, "повторная покупка запрещена")

    king = await svc.get_title_by_code("king")
    ok, reason = await svc.buy_with_coins(111, king["id"])
    check(not ok and "Не хватает" in reason, "не хватает монет — понятная ошибка")

    print("Кастомный эмодзи")
    emojis = await svc.emoji_list()
    check(len(emojis) >= 30, "базовый набор эмодзи загружен")
    await svc.set_emoji(111, emojis[3]["id"])
    check((await svc.get_user(111))["emoji_id"] == emojis[3]["id"], "эмодзи сохранён")

    print("Промокоды")
    dragon = await svc.get_title_by_code("dragon")
    await svc.create_promo("START777", kind="both", coins=300, title_id=dragon["id"], uses_left=200, per_user=1)
    await svc.ensure_user(222, "bob", "Bob")
    ok, status, promo = await svc.redeem_promo(222, "start777")
    check(ok and "coins" in status and "title" in status, "промокод выдал монеты и титул")
    check(await svc.owns(222, dragon["id"]), "титул из промокода выдан")
    ok, status, _ = await svc.redeem_promo(222, "START777")
    check(not ok and status == "already_used", "повторная активация заблокирована")
    ok, status, _ = await svc.redeem_promo(222, "NOPE")
    check(not ok and status == "not_found", "несуществующий код отклонён")

    await svc.create_promo("ONE", kind="coins", coins=10, uses_left=1)
    await svc.ensure_user(333, "carol", "Carol")
    await svc.ensure_user(444, "dave", "Dave")
    ok, _, _ = await svc.redeem_promo(333, "ONE")
    check(ok, "первая активация лимитированного кода")
    ok, status, _ = await svc.redeem_promo(444, "ONE")
    check(not ok and status == "exhausted", "лимит активаций исчерпан")
    check(await svc.deactivate_promo("START777"), "промокод отключается")

    print("Массовая выдача")
    for uid in (111, 222, 333, 444):
        await svc.touch_chat(-1001, uid)
    targets = await svc.bulk_targets(-1001, "chat")
    check(len(targets) == 4, "аудитория чата собрана")
    memer = await svc.get_title_by_code("memer")
    given, total = await svc.bulk_give(targets, memer["id"], chat_id=-1001, actor_id=111, source="mass:chat")
    check(total == 4 and given == 4, "массовая выдача всем")
    given, total = await svc.bulk_give(targets, memer["id"], chat_id=-1001, actor_id=111, source="mass:chat")
    check(given == 0, "повторная массовая выдача не дублирует")

    print("Забрать титул / добавить титул / премиум-эмодзи")
    check(await svc.revoke_title(111, memer["id"]), "титул забран")
    check(not await svc.revoke_title(111, memer["id"]), "повторный отзыв — false")
    new_id = await svc.add_title("cyber", "Кибер-ниндзя", "epic", "🥷", 5000, 0, -1)
    check((await svc.get_title(new_id))["name"] == "Кибер-ниндзя", "новый титул в каталоге")
    added = await svc.add_emoji_set("TestSet", [("😀", "5368324170671202286"), ("😀", "5368324170671202286")])
    check(added == 1, "дубликаты премиум-эмодзи не добавляются")

    print("Статистика")
    s = await svc.stats()
    check(s["users"] >= 4 and s["issued"] >= 4, f"статистика считается: {s}")

    await db.close_db()
    print(f"\nВсе проверки пройдены: {PASSED}")


async def run_all() -> None:
    try:
        await main()
    finally:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(run_all())
    sys.stdout.flush()
    os._exit(0)  # aiosqlite держит фоновый поток — выходим жёстко
