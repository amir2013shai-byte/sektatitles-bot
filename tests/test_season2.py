"""Тесты второго сезона: квесты (включая секретные), казино, дуэли, крафт, лутбоксы, аукцион, обмен, сезоны."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import activity  # noqa: E402
import db  # noqa: E402
import economy  # noqa: E402
import quests  # noqa: E402
import services as svc  # noqa: E402

PASSED = 0


def check(cond: bool, label: str) -> None:
    global PASSED
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    PASSED += 1
    print(f"  ✓ {label}")


async def main() -> None:
    path = os.path.join(tempfile.mkdtemp(), "s2.sqlite3")
    # Проверяем миграцию с уже существующей старой таблицей аукционов (без seller_id).
    legacy = sqlite3.connect(path)
    legacy.execute("""CREATE TABLE auctions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title_id INTEGER NOT NULL,
        start_price INTEGER NOT NULL, top_user INTEGER, top_bid INTEGER NOT NULL DEFAULT 0,
        ends_at TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0, created_by INTEGER
    )""")
    legacy.commit()
    legacy.close()
    await db.init_db(path)
    cols = {r["name"] for r in await db.fetch_all("PRAGMA table_info(auctions)")}
    check("seller_id" in cols, "старая база мягко мигрирована без потери таблиц")
    U = 1001
    await svc.ensure_user(U, "tester", "Tester")
    await svc.add_coins(U, 100_000)

    print("Квесты: обычные")
    for _ in range(10):
        await quests.on_event(U, "messages", 1)
    rows = await quests.visible_progress(U)
    first = next(r for r in rows if r[0].id == "msg_10")
    check(first[1] == 10, "прогресс квеста «10 сообщений» = 10")
    ok, reason, quest = await quests.claim(U, "msg_10")
    check(ok and quest.coins == 100, "награда за квест забрана")
    ok2, reason2, _ = await quests.claim(U, "msg_10")
    check(not ok2 and reason2 == "already", "повторно забрать нельзя")
    check((await svc.get_user(U))["coins"] >= 100_000 + 100, "монеты начислены")

    print("Квесты: секретные")
    visible_ids = {q.id for q in quests.VISIBLE}
    check("s_letter_a" not in visible_ids, "секретный квест отсутствует в видимом списке")
    public_titles = {t["code"] for t in await svc.all_titles()}
    check("alphabet" not in public_titles and "egoist" not in public_titles,
          "секретные наградные титулы скрыты из магазина/публичного каталога")
    check(quests.ALL["s_letter_a"].target == 100000, "секрет про «а» требует 100 000 разных сообщений")
    one_message_user = 1999
    await svc.ensure_user(one_message_user)
    await quests.record_message(one_message_user, "я" * 1000)
    check(await quests.get_counter(one_message_user, "letter_messages:я") == 1,
          "1000 букв в одном сообщении считаются как одно сообщение")
    secrets_done = []
    for _ in range(700):
        secrets_done += await quests.record_message(U, "я")
    check(any(q.id == "s_letter_ya" for q in secrets_done), "секретный квест «Эгоист» сработал сам")
    check(await svc.get_title_by_code("egoist") is not None, "титул за секретный квест есть в каталоге")
    owned_codes = {t["code"] for t in await svc.inventory(U)}
    check("egoist" in owned_codes, "секретный титул выдан")

    print("Казино")
    start = (await svc.get_user(U))["coins"]
    results = [await economy.casino_dice(U, 100) for _ in range(15)]
    check(all(r["ok"] for r in results), "15 ставок в кубик прошли")
    check(any(r["win"] for r in results) and any(not r["win"] for r in results),
          "казино даёт и победы, и проигрыши")
    bad = await economy.casino_dice(U, 1)
    check(not bad["ok"] and "Минимальная" in bad["error"], "ставка ниже минимума отклонена")
    broke = await economy.casino_dice(999_999, 10_000)
    check(not broke["ok"] and "Недостаточно" in broke["error"], "ставка без денег отклонена")

    print("Дуэли")
    V = 1002
    await svc.ensure_user(V, "victim", "Victim")
    await svc.add_coins(V, 5000)
    ok, reason, duel_id = await economy.create_duel(-100, U, V, 500)
    check(ok, f"дуэль создана ({reason})")
    res = await economy.accept_duel(duel_id, V)
    check(res["ok"] and res["pot"] == 1000, "дуэль завершилась, банк 1000")
    check(res["winner"] in (U, V), "победитель определён")
    ok2, _, duel2 = await economy.create_duel(-100, U, V, 300)
    check(await economy.decline_duel(duel2, V), "отказ от дуэли работает")

    print("Крафт")
    for code in ("novice", "reader", "memer"):
        t = await svc.get_title_by_code(code)
        await svc.give_title(U, t["id"], source="test")
    before = len(await svc.inventory(U))
    res = await economy.craft(U, "common")
    check(res["ok"], "крафт обычных титулов удался")
    check(len(await svc.inventory(U)) == before - 2, "3 титула превратились в 1 (−2 в коллекции)")
    res_bad = await economy.craft(U, "mythic")
    check(not res_bad["ok"], "крафт мифических запрещён")

    print("Лутбоксы")
    res = await economy.lootbox(U)
    check(res["ok"], "лутбокс открылся")
    res_poor = await economy.lootbox(999_998)
    check(not res_poor["ok"], "лутбокс без денег отклонён")

    print("Аукцион")
    W = 1003
    await svc.ensure_user(W, "bidder", "Bidder")
    seller_before = (await svc.get_user(U))["coins"]
    bidder_before = (await svc.get_user(V))["coins"]
    king = await svc.get_title_by_code("king")
    await svc.give_title(U, king["id"], source="test")
    await svc.add_coins(W, 5000)
    ok, reason, aid = await economy.create_auction(U, king["id"], 1000, 24)
    check(ok and aid > 0, f"свой лот создан ({reason})")
    check(not await svc.owns(U, king["id"]), "титул продавца зарезервирован в эскроу")
    bid = await economy.place_bid(aid, V, 1000)
    check(bid["ok"], "первая ставка принята")
    low = await economy.place_bid(aid, W, 500)
    check(not low["ok"], "ставка ниже минимальной отклонена")
    higher = await economy.place_bid(aid, W, 1500)
    check(higher["ok"], "перебили ставку")
    check((await svc.get_user(V))["coins"] == bidder_before, "перебитая ставка автоматически возвращена")
    closed = await economy.close_auction(aid)
    check(closed and closed["sold"] and closed["winner"] == W, "аукцион закрыт в пользу лидера")
    check(await svc.owns(W, king["id"]), "победитель получил титул")
    check((await svc.get_user(U))["coins"] == seller_before + 1425, "продавец получил выручку за вычетом 5% комиссии")

    print("Аренда")
    rent_title = await svc.add_title("rent_test", "Титул напрокат", "rare", "⏳", 0, 0, -1)
    await svc.give_title(U, rent_title, source="test")
    await svc.add_coins(V, 1000)
    owner_before = (await svc.get_user(U))["coins"]
    ok, reason, rent_id = await economy.create_rental(U, rent_title, 100, 1)
    check(ok, f"арендный лот выставлен ({reason})")
    check(not await svc.owns(U, rent_title), "титул владельца зарезервирован")
    rented = await economy.start_rental(rent_id, V)
    check(rented["ok"], "аренда оплачена и началась")
    renter_item = next(t for t in await svc.inventory(V) if t["id"] == rent_title)
    check(renter_item["inventory_source"] == f"rent:{rent_id}", "арендованный титул помечен сроком")
    check((await svc.get_user(U))["coins"] == owner_before + 95, "владелец получил плату за вычетом 5%")
    rejected_trade = await economy.propose_trade(-100, V, U, rent_title, rent_title)
    check(not rejected_trade[0], "арендованный титул нельзя обменять")
    await db.execute("UPDATE rentals SET ends_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (rent_id,))
    check(await economy.expire_rentals() == 1, "истёкшая аренда автоматически завершена")
    check(not await svc.owns(V, rent_title) and await svc.owns(U, rent_title),
          "после срока титул снят с арендатора и возвращён владельцу")

    print("Обмен")
    a = await svc.add_title("trade_a", "Тестовый А", "rare", "🦊", 0, 0, -1)
    b = await svc.add_title("trade_b", "Тестовый Б", "epic", "🦄", 0, 0, -1)
    await svc.give_title(V, a, source="test")
    await svc.give_title(U, b, source="test")
    ok, reason, trade_id = await economy.propose_trade(-100, V, U, a, b)
    check(ok, f"обмен предложен ({reason})")
    res = await economy.accept_trade(trade_id, U)
    check(res["ok"], "обмен принят")
    check(await svc.owns(U, a) and await svc.owns(V, b), "титулы перешли владельцам")

    print("Сезоны")
    sid = await activity.start_season("Тестовый", days=1)
    check(sid > 0, "сезон запущен")
    await activity.add_season_points(U, 25)
    season, top = await activity.season_top(3)
    check(top and top[0]["user_id"] == U and top[0]["points"] == 25, "очки сезона начислены")
    season_id, winners = await activity.close_season()
    check(bool(winners), "сезон закрыт с победителями")
    check(await svc.get_title_by_code("season_gold") is not None, "титул чемпиона есть")

    print("Автонаграды за активность")
    await svc.ensure_user(W, "chatty", "Chatty")
    for _ in range(12):
        await activity.bump_activity(-100, W)
    given = await activity.check_auto_titles(W)
    check(bool(given), f"автотитул за 12 сообщений выдан: {[t['name'] for t in given]}")
    given2 = await activity.check_auto_titles(W)
    check(not given2, "повторно тот же автотитул не выдаётся")
    # Считаются 7 различных активных дней, а не 7 сообщений в один день.
    for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
                "2026-01-05", "2026-01-06", "2026-01-07"):
        await db.execute(
            "INSERT OR IGNORE INTO activity_daily (chat_id, user_id, day, messages) VALUES (?, ?, ?, 1)",
            (-100, W, day))
    given_days = await activity.check_auto_titles(W)
    check(any(t["code"] == "active_week" for t in given_days), "титул за 7 активных дней выдан")

    print("ИИ-генератор")
    import aigen

    spec = aigen.generate("неоновый дракон из подворотни")
    check(spec["emoji"] == "🐉" and spec["rarity"] == "legendary", "генератор подобрал эмодзи и редкость")
    check(len(spec["name"]) <= 24, "название не длиннее 24 символов")
    codes = aigen.campaign("sekta", count=3, coins=250)
    check(len(codes) == 3 and all(c["code"].isalnum() for c in codes), "кампания промокодов сгенерирована")
    pack = aigen.emoji_pack("дракон огонь космос")
    check("🐉" in pack and "🔥" in pack, "генератор подобрал тематический набор эмодзи")

    print(f"\nВсе проверки второго сезона пройдены: {PASSED}")


async def run() -> None:
    try:
        await main()
    finally:
        await db.close_db()


if __name__ == "__main__":
    code = 0
    try:
        asyncio.run(run())
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
