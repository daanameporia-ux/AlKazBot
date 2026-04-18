"""Bot entrypoint — aiogram Dispatcher + long-polling runner.

Launch locally:
    uv run python -m src.bot.main

On Railway: defined in `railway.toml` → `startCommand`.

Long-polling is used instead of webhooks for MVP simplicity (Railway supports
both; webhooks require a public HTTPS endpoint which we can add later).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys

import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from src.bot.handlers import router as root_router
from src.bot.middlewares import MessageLoggingMiddleware, WhitelistMiddleware
from src.config import settings
from src.logging_setup import configure_logging, get_logger


def _init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
        send_default_pii=False,
    )


BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Поздороваться / регистрация"),
    BotCommand(command="help", description="Что я умею"),
    BotCommand(command="report", description="Собрать вечерний отчёт"),
    BotCommand(command="balance", description="Быстрый снапшот балансов"),
    BotCommand(command="stock", description="Что на складе"),
    BotCommand(command="fx", description="Текущий курс RUB→USDT"),
    BotCommand(command="partners", description="Доли партнёров"),
    BotCommand(command="knowledge", description="База знаний бота"),
    BotCommand(command="feedback", description="Пожелания команды"),
    BotCommand(command="history", description="Последние операции"),
    BotCommand(command="chatid", description="Показать chat_id"),
]


async def _setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)


async def _runner() -> None:
    print("[boot] _runner() entered", flush=True)
    configure_logging()
    log = get_logger(__name__)
    _init_sentry()

    log.info(
        "bot_starting",
        app_env=settings.app_env,
        model=settings.anthropic_model,
        allowed_users=len(settings.allowed_tg_user_ids),
    )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Outer middleware: whitelist (drops unauthorized updates).
    dp.message.outer_middleware(WhitelistMiddleware())
    dp.callback_query.outer_middleware(WhitelistMiddleware())
    # Inner middleware: persist every surviving message.
    dp.message.middleware(MessageLoggingMiddleware())

    dp.include_router(root_router)

    await _setup_commands(bot)

    # Graceful shutdown on SIGTERM (Railway redeploys send it).
    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        log.info("bot_shutdown_requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: no signal support
            loop.add_signal_handler(sig, _stop)

    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        name="aiogram-polling",
    )

    await stop_event.wait()
    await dp.stop_polling()
    polling_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await polling_task
    await bot.session.close()
    log.info("bot_stopped")


def main() -> None:
    print("[boot] main() called", flush=True)
    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:
        sys.exit(0)
    except BaseException as e:  # noqa: BLE001
        # Last-ditch visibility on startup-crash in Railway logs.
        print(f"[boot] fatal: {e.__class__.__name__}: {e}", flush=True)
        raise


if __name__ == "__main__":
    print("[boot] entry module loaded", flush=True)
    main()
