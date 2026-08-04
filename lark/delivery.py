"""Outbound Lark text, card, and final-image delivery."""

from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .adapter import ParsedMessage
from .presentation import _final_card, _RunTimingStats

SendText = Callable[[ParsedMessage, str], Awaitable[None]]
SendCard = Callable[[ParsedMessage, dict[str, Any]], Awaitable[str | None]]
SendAttachments = Callable[
    [ParsedMessage, list[dict[str, Any]], _RunTimingStats],
    Awaitable[None],
]


async def send_text_reply(
    parsed: ParsedMessage,
    text: str,
    *,
    client_factory: Callable[[], Any],
) -> None:
    """Reply in a group thread or send a direct message."""
    message = (parsed.raw.get("event") or {}).get("message") or {}
    try:
        if message.get("chat_type") == "group" and (
            message_id := message.get("message_id")
        ):
            await client_factory().reply_text(message_id, text)
        else:
            await client_factory().send_text(parsed.user_id, text)
    except Exception as exc:
        print(
            f"[lark] send text failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


async def send_card(
    parsed: ParsedMessage,
    card: dict[str, Any],
    *,
    client_factory: Callable[[], Any],
    failure_label: str,
) -> str | None:
    """Reply with a card in groups or send one directly."""
    message = (parsed.raw.get("event") or {}).get("message") or {}
    try:
        if message.get("chat_type") == "group" and (
            message_id := message.get("message_id")
        ):
            response = await client_factory().reply_card(message_id, card)
        else:
            response = await client_factory().send_card(parsed.user_id, card)
        return (response.get("data") or {}).get("message_id")
    except Exception as exc:
        print(
            f"[lark] {failure_label} failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


async def upload_final_card_images(
    attachments: list[dict[str, Any]],
    timings: _RunTimingStats,
    *,
    upload_images: Callable[..., Awaitable[tuple[list[dict], list[dict]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Upload card images and retain failed items for separate delivery."""
    if not attachments:
        return [], []
    started_at = time.monotonic()
    card_images, failed = await upload_images(
        attachments,
        context="final_card",
    )
    timings.record_lark(
        "upload_final_card_images",
        (time.monotonic() - started_at) * 1000,
    )
    return failed, card_images


async def send_uploaded_image_fallbacks(
    parsed: ParsedMessage,
    card_images: list[dict[str, str]],
    timings: _RunTimingStats,
    *,
    client_factory: Callable[[], Any],
) -> None:
    """Send already-uploaded images when the final card cannot be delivered."""
    if not card_images:
        return
    message = (parsed.raw.get("event") or {}).get("message") or {}
    is_group = message.get("chat_type") == "group" and bool(parsed.chat_id)
    receive_id = parsed.chat_id if is_group else parsed.user_id
    receive_id_type = "chat_id" if is_group else "open_id"
    started_at = time.monotonic()
    try:
        for item in card_images:
            image_key = str(item.get("img_key") or "")
            if image_key:
                await client_factory().send_image(
                    receive_id,
                    image_key,
                    receive_id_type=receive_id_type,
                )
    except Exception as exc:
        print(
            f"[lark] send uploaded image fallback failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    finally:
        timings.record_lark(
            "send_uploaded_image_fallbacks",
            (time.monotonic() - started_at) * 1000,
        )


async def send_final_image_attachments(
    parsed: ParsedMessage,
    attachments: list[dict[str, Any]],
    timings: _RunTimingStats,
    *,
    send_images: Callable[..., Awaitable[None]],
) -> None:
    """Send final visual evidence that could not be embedded in a card."""
    if not attachments:
        return
    try:
        message = (parsed.raw.get("event") or {}).get("message") or {}
        is_group = message.get("chat_type") == "group" and bool(parsed.chat_id)
        started_at = time.monotonic()
        await send_images(
            parsed.chat_id if is_group else parsed.user_id,
            attachments,
            context="final_visual",
            receive_id_type="chat_id" if is_group else "open_id",
        )
        timings.record_lark(
            "send_final_visuals",
            (time.monotonic() - started_at) * 1000,
        )
    except Exception as exc:
        print(
            f"[lark] send final visual outputs failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


async def send_streaming_final(
    parsed: ParsedMessage,
    *,
    final: str,
    ok: bool,
    attachments: list[dict[str, Any]],
    card_images: list[dict[str, str]],
    timings: _RunTimingStats,
    send_final_card: SendCard,
    send_text: SendText,
    send_uploaded_images: SendAttachments,
    send_final_attachments: SendAttachments,
) -> None:
    """Send the terminal card, with complete text and image fallbacks."""
    try:
        started_at = time.monotonic()
        message_id = await send_final_card(
            parsed,
            _final_card(final, ok=ok, image_blocks=card_images),
        )
        timings.record_lark(
            "send_final_card",
            (time.monotonic() - started_at) * 1000,
        )
        if message_id is None:
            raise RuntimeError("final card message id missing")
    except Exception:
        started_at = time.monotonic()
        await send_text(parsed, final)
        timings.record_lark(
            "send_final_text_fallback",
            (time.monotonic() - started_at) * 1000,
        )
        await send_uploaded_images(parsed, card_images, timings)
    if attachments:
        await send_final_attachments(parsed, attachments, timings)
