"""
Проверка HTTP-части (как на Render/Railway): healthcheck и приём вебхука.
Поднимаем aiohttp-приложение на порту 8099 с фейковой сессией бота.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiohttp import ClientSession, web

from aiogram import Bot

import db
from config import config
from main import build_dispatcher, build_web_app
from test_bot_smoke import FakeSession


async def main() -> None:
    await db.init_db(os.path.join(tempfile.mkdtemp(), "wh.sqlite3"))
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = build_dispatcher()
    app = build_web_app(bot, dp)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8099)
    await site.start()

    async with ClientSession() as http:
        r = await http.get("http://127.0.0.1:8099/")
        assert r.status == 200 and (await r.text()) == "ok", "healthcheck не отвечает"
        print("  ✓ healthcheck GET / → 200 ok")

        r = await http.get("http://127.0.0.1:8099/health")
        assert r.status == 200, "GET /health не отвечает"
        print("  ✓ healthcheck GET /health → 200")

        update = {
            "update_id": 7,
            "message": {
                "message_id": 1,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": 777, "type": "private"},
                "from": {"id": 777, "is_bot": False, "first_name": "Vasya", "username": "vasya"},
                "text": "/start",
            },
        }
        r = await http.post(
            f"http://127.0.0.1:8099{config.webhook_path}",
            data=json.dumps(update),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert r.status == 401, f"вебхук без секрета должен отдавать 401, а отдал {r.status}"
        print("  ✓ неверный секрет вебхука → 401")

        r = await http.post(
            f"http://127.0.0.1:8099{config.webhook_path}",
            data=json.dumps(update),
            headers={"X-Telegram-Bot-Api-Secret-Token": config.webhook_secret},
        )
        assert r.status == 200, f"вебхук не обработал апдейт: {r.status}"
        sent = [c for c in session.calls if type(c).__name__ == "SendMessage"]
        assert sent, "бот не ответил на /start через вебхук"
        print("  ✓ вебхук с верным секретом обработал /start и ответил в чат")

    await runner.cleanup()
    await db.close_db()
    await bot.session.close()
    print("\nHTTP/вебхук проверен.")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    os._exit(0)
