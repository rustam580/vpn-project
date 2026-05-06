"""Message-sending helpers for status, configs, and device links.

These functions wrap aiogram `Message.answer` / `Bot.send_message` calls
that build user-facing text from Marzban data. Extracted out of
`bot_runtime` so the runtime stays focused on dispatcher wiring.
"""

from __future__ import annotations

import html
from typing import Any, Mapping

from aiogram import Bot
from aiogram.types import Message

from config import Settings
from models import MarzbanUser
from src.vpnbot.bot_access import ensure_device
from src.vpnbot.device_utils import _device_label
from src.vpnbot.db.bot_repo import Repo
from src.vpnbot.message_utils import config_import_hint_text
from src.vpnbot.services.bot_marzban import MarzbanClient
from utils import extract_links, select_delivery_links, status_text


async def send_status(message: Message, user: Mapping[str, Any] | MarzbanUser) -> None:
    await message.answer(status_text(user), parse_mode="HTML")


async def send_status_to_bot(
    bot: Bot, telegram_id: int, user: Mapping[str, Any] | MarzbanUser
) -> None:
    await bot.send_message(telegram_id, status_text(user), parse_mode="HTML")


async def send_links(message: Message, user: Mapping[str, Any] | MarzbanUser) -> None:
    links = extract_links(user)
    if not links:
        await message.answer("⚠️ Конфиг не найден в ответе Marzban. Попробуйте позже.")
        return
    await message.answer("🔑 Ваша ссылка подключения (1 устройство):")
    link = links[0]
    safe_link = html.escape(link)
    text = f"<code>{safe_link}</code>"
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    await message.answer(config_import_hint_text(), parse_mode="HTML")


async def collect_device_links(
    *,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> list[tuple[int, str, str]]:
    devices = await repo.list_devices(telegram_id)
    if not devices:
        _, user, _ = await ensure_device(
            telegram_id=telegram_id,
            device_id=1,
            repo=repo,
            marzban=marzban,
            settings=settings,
            create_if_missing=False,
        )
        if not user:
            return []
        links = select_delivery_links(
            user,
            mode=settings.config_delivery_mode,
            public_base_url=settings.subscription_public_base_url,
        )
        label = _device_label(1, None)
        return [(1, label, link) for link in links]

    result: list[tuple[int, str, str]] = []
    for row in devices:
        device_id = int(row["device_id"])
        username = str(row["marzban_username"])
        label = _device_label(device_id, row.get("device_name"))
        user = await marzban.get_user(username)
        if not user:
            continue
        status = str(user.get("status", "unknown"))
        if status != "active":
            continue
        links = select_delivery_links(
            user,
            mode=settings.config_delivery_mode,
            public_base_url=settings.subscription_public_base_url,
        )
        for link in links:
            result.append((device_id, label, link))
    return sorted(result, key=lambda item: (item[0], item[2]))


async def send_device_links(
    *,
    message: Message,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> None:
    items = await collect_device_links(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
    )
    if not items:
        await message.answer("⚠️ Активные ссылки подключения не найдены.")
        return

    await message.answer(
        f"🔑 Ниже ваши активные ссылки для подключения ({len(items)}).\n"
        "Нажмите на ссылку, чтобы скопировать."
    )
    await send_configs_in_chat(message, items)


def _render_config_block(label: str, link: str) -> str:
    safe_label = html.escape(label)
    safe_link = html.escape(link)
    return f"{safe_label}:\n<code>{safe_link}</code>"


async def send_configs_in_chat(message: Message, items: list[tuple[int, str, str]]) -> None:
    if not items:
        await message.answer("⚠️ Активные ссылки подключения не найдены.")
        return
    chunks: list[str] = []
    current = ""
    for _, label, link in items:
        block = _render_config_block(label, link)
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > 3500 and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


async def send_configs_in_chat_to_bot(
    *,
    bot: Bot,
    telegram_id: int,
    items: list[tuple[int, str, str]],
) -> None:
    if not items:
        await bot.send_message(telegram_id, "⚠️ Активные ссылки подключения не найдены.")
        return
    chunks: list[str] = []
    current = ""
    for _, label, link in items:
        block = _render_config_block(label, link)
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > 3500 and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await bot.send_message(telegram_id, chunk, parse_mode="HTML", disable_web_page_preview=True)


async def send_device_links_to_bot(
    *,
    bot: Bot,
    telegram_id: int,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> None:
    items = await collect_device_links(
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
    )
    if not items:
        await bot.send_message(telegram_id, "⚠️ Активные ссылки подключения не найдены.")
        return
    await bot.send_message(
        telegram_id,
        f"🔑 Ниже ваши активные ссылки для подключения ({len(items)}).\n"
        "Нажмите на ссылку, чтобы скопировать.",
    )
    await send_configs_in_chat_to_bot(bot=bot, telegram_id=telegram_id, items=items)


async def notify_access_updated(
    bot: Bot,
    telegram_id: int,
    user: dict[str, Any],
    text: str,
    *,
    repo: Repo,
    marzban: MarzbanClient,
    settings: Settings,
) -> None:
    await bot.send_message(telegram_id, text)
    await send_status_to_bot(bot, telegram_id, user)
    await send_device_links_to_bot(
        bot=bot,
        telegram_id=telegram_id,
        repo=repo,
        marzban=marzban,
        settings=settings,
    )
