import asyncio
import contextlib
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

import db
import remna as marzban  # compatibility adapter over Remnawave

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vpn_bot")


async def panel_keepalive_loop():
    """
    Фоновый прогрев Remnawave:
    - поддерживает свежий токен
    - снижает шанс "первый запрос после простоя долгий"
    """
    while True:
        try:
            # важно: в marzban.py должна быть функция keepalive()
            await marzban.keepalive()
        except Exception:
            logger.exception("Remnawave keepalive failed")
        await asyncio.sleep(600)  # раз в 10 минут


async def main():
    # Загружаем .env из текущей директории ДО импорта handlers,
    # чтобы все настройки через os.getenv() подхватились корректно.
    base_dir = Path(__file__).resolve().parent
    load_dotenv(base_dir / ".env")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан в .env")

    # Гарантируем наличие таблиц/миграций до запуска фоновых задач
    db.init_db()

    # Импортируем handlers после load_dotenv()
    from handlers import router as main_router, background_worker

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    # Защита от спама, слишком быстрых кнопок и двойных
    # финансовых операций.
    from protection import AntiFloodMiddleware

    protection = AntiFloodMiddleware()
    dp.message.outer_middleware(protection)
    dp.callback_query.outer_middleware(protection)

    dp.include_router(main_router)

    # Публичный callback Platega слушает локально; HTTPS завершает Nginx.
    import platega
    platega_runner = await platega.start_callback_server(bot)

    # Бизнес-уведомления во второй Telegram-бот:
    # триалы, Stars, покупки, продления и ошибки.
    from admin_notify import (
        AdminNotifier,
        TelegramErrorHandler,
    )

    admin_notifier = AdminNotifier(
        base_dir / "vpn_bot.db"
    )

    admin_error_handler = TelegramErrorHandler(
        admin_notifier,
        asyncio.get_running_loop(),
    )

    logging.getLogger().addHandler(
        admin_error_handler
    )

    admin_notify_task = asyncio.create_task(
        admin_notifier.run()
    )

    # Постепенная сверка локальной SQLite с Remnawave.
    from reconcile import reconciliation_loop

    reconcile_task = asyncio.create_task(
        reconciliation_loop(admin_notifier)
    )

    # В тестовой сборке фоновые отключения/автопродления выключены по умолчанию.
    background_enabled = os.getenv("BACKGROUND_WORKER_ENABLED", "false").lower() in (
        "1", "true", "yes", "on"
    )
    bg_task = (
        asyncio.create_task(background_worker(bot))
        if background_enabled
        else None
    )

    # Keepalive к Remnawave (API-токен/сессия прогреты в фоне).
    ka_task = asyncio.create_task(panel_keepalive_loop())

    logger.info("Бот запущен.")
    try:
        # Telegram command menu
        await bot.set_my_commands(
            [
                BotCommand(
                    command="start",
                    description="Главное меню",
                ),
                BotCommand(
                    command="help",
                    description="Помощь и документы",
                ),
            ]
        )

        await dp.start_polling(bot)
    finally:
        tasks = [task for task in (bg_task, ka_task, admin_notify_task) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Корректно останавливаем сверку Remnawave.
        reconcile_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconcile_task

        with contextlib.suppress(Exception):
            logging.getLogger().removeHandler(
                admin_error_handler
            )

        with contextlib.suppress(Exception):
            await admin_notifier.close()

        if platega_runner is not None:
            with contextlib.suppress(Exception):
                await platega_runner.cleanup()

        with contextlib.suppress(Exception):
            await marzban.close()

        with contextlib.suppress(Exception):
            await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
