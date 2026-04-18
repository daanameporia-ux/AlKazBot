"""Aggregates all routers into one top-level `router`."""

from aiogram import Router

from src.bot.handlers import (
    admin,
    callbacks,
    commands,
    documents,
    mentions,
    messages,
    photo,
    stickers,
    voice,
)

router = Router(name="root")
router.include_router(commands.router)
# Media-specific routers BEFORE the text-catchers so they claim their
# message types without being shadowed.
router.include_router(documents.router)
router.include_router(photo.router)
router.include_router(voice.router)
router.include_router(stickers.router)
# Trigger-path for text messages (@-mention / reply to bot).
router.include_router(mentions.router)
router.include_router(callbacks.router)
router.include_router(admin.router)
# Catch-all for passive text — buffered into BatchBuffer.
router.include_router(messages.router)
