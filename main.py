"""
Точка входа. Режимы:
  • polling  — если PUBLIC_BASE_URL не задан (локальный запуск / VPS без домена)
  • webhook  — если задан PUBLIC_BASE_URL (Render, Railway, Fly.io и т.п.)

Запуск:  python main.py
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, Update

import db
import economy
from config import config
import backup
from handlers import admin, ai, common, games, market, quests_h, season, shop, style
import handlers_hub
from middlewares import UserTracking

log = logging.getLogger("titlebot")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UserTracking())

    # Порядок важен: команды админов — раньше «ловушек» текста
    dp.include_router(admin.router)
    dp.include_router(ai.router)
    dp.include_router(backup.router)
    dp.include_router(style.router)
    dp.include_router(games.router)
    dp.include_router(quests_h.router)
    dp.include_router(market.router)
    dp.include_router(season.router)
    dp.include_router(handlers_hub.router)
    dp.include_router(shop.router)
    dp.include_router(common.router)   # здесь живёт F.text.regexp для промокодов

    @dp.error()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
            return True
        log.exception("update failed", exc_info=exc)
        return True

    return dp


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Меню"),
        BotCommand(command="profile", description="Профиль и титул"),
        BotCommand(command="shop", description="Магазин титулов"),
        BotCommand(command="style", description="Эмодзи титула"),
        BotCommand(command="promo", description="Активировать промокод"),
        BotCommand(command="top", description="Топ коллекционеров"),
        BotCommand(command="quests", description="Задания и награды"),
        BotCommand(command="season", description="Сезон и лидерборд"),
        BotCommand(command="dice", description="Казино: кубик на монеты"),
        BotCommand(command="coin", description="Казино: монетка"),
        BotCommand(command="duel", description="Дуэль на монеты"),
        BotCommand(command="craft", description="Крафт титулов"),
        BotCommand(command="lootbox", description="Открыть лутбокс"),
        BotCommand(command="auction", description="Аукцион титулов"),
        BotCommand(command="bid", description="Ставка на лот"),
        BotCommand(command="trade", description="Предложить обмен титулами"),
        BotCommand(command="rentals", description="Аренда титулов"),
        BotCommand(command="rent", description="Сдать или арендовать титул"),
        BotCommand(command="showcase", description="Витрина коллекции"),
        BotCommand(command="aititle", description="Создать титул-генератором (владелец)"),
        BotCommand(command="aipromo", description="Создать промокампанию (владелец)"),
        BotCommand(command="aiemoji", description="Добавить тематический набор эмодзи"),
        BotCommand(command="admin", description="Админ-панель"),
        BotCommand(command="whoami", description="Проверить свои права"),
        BotCommand(command="help", description="Помощь"),
    ])


def build_web_app(bot: Bot, dp: Dispatcher):
    """aiohttp-приложение: healthcheck + приём вебхука Telegram."""
    from aiohttp import web

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def hook(request: web.Request) -> web.Response:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.webhook_secret:
            raise web.HTTPUnauthorized()
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dp.feed_update(bot, update)
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post(config.webhook_path, hook)
    return app


async def run_webhook(bot: Bot, dp: Dispatcher) -> None:
    from aiohttp import web

    app = build_web_app(bot, dp)
    url = f"{config.public_base_url}{config.webhook_path}"
    await bot.set_webhook(
        url,
        secret_token=config.webhook_secret,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    log.info("Владельцы бота: ids=%s usernames=%s", config.owner_ids, config.owner_usernames)
    log.info("Webhook установлен: %s", url)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.port)
    await site.start()
    log.info("HTTP-сервер слушает 0.0.0.0:%s", config.port)
    await asyncio.Event().wait()


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Владельцы бота: ids=%s usernames=%s", config.owner_ids, config.owner_usernames)
    log.info("Запуск в режиме polling (без PUBLIC_BASE_URL)")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.token:
        raise SystemExit(
            "Не задан BOT_TOKEN.\n"
            "1) Создайте бота у @BotFather → /newbot\n"
            "2) Скопируйте токен в переменную окружения BOT_TOKEN (или в файл .env)"
        )

    await db.init_db(config.db_path)

    bot = Bot(
        token=config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    await set_commands(bot)

    interval = float(os.getenv("AUTO_BACKUP_HOURS", "0") or 0)
    background: list[asyncio.Task] = [
        asyncio.create_task(economy.maintenance_loop(), name="economy-maintenance")
    ]
    if interval > 0:
        background.append(asyncio.create_task(backup.auto_backup_loop(bot, interval), name="auto-backup"))
    try:
        if config.use_webhook:
            await run_webhook(bot, dp)
        else:
            await run_polling(bot, dp)
    finally:
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            print(exc.code)
