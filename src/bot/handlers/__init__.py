"""Aggregates all routers into one top-level `router`."""

from aiogram import Router

from src.bot.handlers import admin, callbacks, commands, mentions, messages

router = Router(name="root")
router.include_router(commands.router)
router.include_router(mentions.router)
router.include_router(callbacks.router)
router.include_router(admin.router)
# Catch-all — must be last so specific routers get first pick.
router.include_router(messages.router)
