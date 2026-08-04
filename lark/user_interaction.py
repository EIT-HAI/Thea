"""User-interaction Tool adapters and channel image delivery."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from harness import BuiltinTool

LoadImage = Callable[[dict[str, Any]], Awaitable[tuple[bytes, str]]]
ImageFilename = Callable[[str, int, str], str]
ClientFactory = Callable[[], Any]


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_str_list(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if str(item or "").strip()]


def normalize_observation_view(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def visual_output_to_attachments(item: Any) -> list[dict[str, Any]]:
    """Convert one public visual output into a channel attachment."""
    if isinstance(item, str):
        return [{"kind": "url", "source": item, "caption": ""}]
    if not isinstance(item, dict):
        return []

    caption = str(
        item.get("caption")
        or item.get("label")
        or item.get("object_id")
        or item.get("ref")
        or item.get("id")
        or ""
    )
    image_url = item.get("image_url")
    url = str(image_url.get("url") or "") if isinstance(image_url, dict) else ""
    url = url or str(item.get("url") or "")
    path = str(item.get("image_path") or item.get("path") or "")
    metadata = {
        field: str(item[field])
        for field in ("color_space", "color_order")
        if item.get(field)
    }
    if url:
        return [{"kind": "url", "source": url, "caption": caption, **metadata}]
    if path:
        return [{"kind": "path", "source": path, "caption": caption, **metadata}]
    return []


def collect_query_user_visuals(
    *,
    max_images: int,
    observation_visuals: list[dict[str, Any]] | None = None,
    image_urls: list[str] | str | None = None,
    image_paths: list[str] | str | None = None,
    visual_outputs: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize trusted image outputs for channel delivery."""
    attachments: list[dict[str, Any]] = []
    for item in observation_visuals or []:
        if isinstance(item, dict):
            attachments.extend(visual_output_to_attachments(item))
    for item in as_list(visual_outputs):
        attachments.extend(visual_output_to_attachments(item))
    for url in as_str_list(image_urls):
        attachments.append({"kind": "url", "source": url, "caption": ""})
    for path in as_str_list(image_paths):
        attachments.append({"kind": "path", "source": path, "caption": ""})

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in attachments:
        source = item.get("source", "").strip()
        if not source:
            continue
        key = (item.get("kind", "url"), source)
        if key in seen:
            continue
        seen.add(key)
        normalized: dict[str, Any] = {
            "kind": item.get("kind", "url"),
            "source": source,
            "caption": item.get("caption", "").strip(),
        }
        for metadata_key in ("color_space", "color_order"):
            if item.get(metadata_key):
                normalized[metadata_key] = str(item[metadata_key])
        deduplicated.append(normalized)
    return deduplicated[:max_images]


def capture_session_observation_event(session: Any, event: dict[str, Any]) -> None:
    """Cache public visual outputs emitted by the latest Observation event."""
    if event.get("type") != "observation":
        return
    outputs = event.get("visual_outputs")
    session.latest_observation_visual_outputs = (
        [dict(item) for item in outputs if isinstance(item, dict)]
        if isinstance(outputs, list)
        else []
    )


def resolve_observation_view_visuals(
    session: Any,
    observation_views: list[str] | str | None,
    *,
    max_images: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve requested names against the latest Observation only."""
    requested_names = as_str_list(observation_views)
    if not requested_names:
        return [], []
    cached = (
        session.latest_observation_visual_outputs
        if isinstance(session.latest_observation_visual_outputs, list)
        else []
    )
    indexed: dict[str, dict[str, Any]] = {}
    for item in cached:
        if not isinstance(item, dict):
            continue
        for key in ("caption", "camera", "view", "label"):
            normalized = normalize_observation_view(item.get(key))
            if normalized:
                indexed.setdefault(normalized, item)

    matches: list[dict[str, Any]] = []
    missing: list[str] = []
    seen_items: set[int] = set()
    for requested_name in requested_names:
        item = indexed.get(normalize_observation_view(requested_name))
        if item is None:
            missing.append(requested_name)
            continue
        identity = id(item)
        if identity not in seen_items:
            matches.append(item)
            seen_items.add(identity)
    return matches[:max_images], missing


def build_query_user_prompt(
    question: str,
    attachments: list[dict[str, Any]],
    candidate_refs: list[str] | None = None,
) -> str:
    lines = [f"❓ {question.strip()}"]
    refs = list(
        dict.fromkeys(
            item.strip() for item in as_str_list(candidate_refs) if item.strip()
        )
    )
    if refs:
        lines.extend(["", "Candidate Scene Graph refs:"])
        lines.extend(f"{index}. {ref}" for index, ref in enumerate(refs, start=1))
    if attachments:
        lines.extend(
            [
                "",
                "Current Observation views attached:",
            ]
        )
        for index, item in enumerate(attachments, start=1):
            caption = item.get("caption") or item.get("source", "")
            lines.append(f"{index}. {caption}")
    if refs:
        reply_hint = "Reply with a candidate ref or another clear description."
    elif attachments:
        reply_hint = "Reply with the image number, object ID, or another description."
    else:
        reply_hint = "Reply to this question."
    lines.extend(["", f"{reply_hint} Send /cancel to stop waiting."])
    return "\n".join(lines)


async def send_image_attachments(
    receive_id: str,
    attachments: list[dict[str, Any]],
    *,
    context: str,
    receive_id_type: str,
    client_factory: ClientFactory,
    load_image: LoadImage,
    image_filename: ImageFilename,
) -> None:
    """Upload and send each attachment without disclosing failed source paths."""
    for index, item in enumerate(attachments, start=1):
        source = item["source"]
        caption = item.get("caption", "")
        try:
            image_bytes, content_type = await load_image(item)
            filename = image_filename(source, index, content_type)
            image_result = await client_factory().send_image_bytes(
                receive_id,
                image_bytes,
                filename=filename,
                content_type=content_type,
                receive_id_type=receive_id_type,
            )
            message_id = (image_result.get("data") or {}).get("message_id", "")
            print(
                f"[{context}] sent image {index}/{len(attachments)} "
                f"message_id={message_id!r} caption={caption!r}",
                file=sys.stderr,
            )
        except Exception:
            label = f"Image {index}"
            if caption:
                label += f" ({caption})"
            await client_factory().send_text(
                receive_id,
                f"{label} upload failed. Capture a fresh image and try again.",
                receive_id_type=receive_id_type,
            )


async def upload_card_images(
    attachments: list[dict[str, Any]],
    *,
    context: str,
    client_factory: ClientFactory,
    load_image: LoadImage,
    image_filename: ImageFilename,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Upload images for a Lark card and return failed attachments separately."""
    images: list[dict[str, str]] = []
    failed: list[dict[str, Any]] = []
    for index, item in enumerate(attachments, start=1):
        source = item["source"]
        caption = item.get("caption", "") or f"image {index}"
        try:
            image_bytes, content_type = await load_image(item)
            filename = image_filename(source, index, content_type)
            image_key = await client_factory().upload_image_bytes(
                image_bytes,
                filename=filename,
                content_type=content_type,
            )
            images.append({"img_key": image_key, "alt": caption})
            print(
                f"[{context}] uploaded card image {index}/{len(attachments)} "
                f"img_key={image_key!r} caption={caption!r}",
                file=sys.stderr,
            )
        except Exception as exc:
            failed.append(item)
            print(
                f"[{context}] upload card image {index}/{len(attachments)} failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return images, failed


def install_query_user(
    session: Any,
    open_id: str,
    main_loop: asyncio.AbstractEventLoop,
    *,
    timeout_sec: int,
    cancel_sentinel: str,
    max_images: int,
    send_payload: Callable[
        [str, str, list[dict[str, Any]]],
        Awaitable[None],
    ],
) -> None:
    """Replace the fallback ``query_user`` with a blocking Lark adapter."""
    schema = next(
        (
            item
            for item in session.harness.registry.list_tool_definitions()
            if item.get("name") == "query_user"
        ),
        None,
    )
    if schema is None:
        return

    def query_user_via_lark(
        question: str = "",
        candidate_refs: list[str] | None = None,
        observation_views: list[str] | str | None = None,
    ) -> dict[str, Any]:
        if not question or not question.strip():
            return {"success": False, "reason": "missing required argument 'question'"}
        visuals, missing = resolve_observation_view_visuals(
            session,
            observation_views,
            max_images=max_images,
        )
        if missing:
            return {
                "success": False,
                "kind": "query_user_observation_view_unavailable",
                "waited_ms": 0,
                "reason": (
                    "requested views are unavailable in the latest Observation: "
                    + ", ".join(missing)
                ),
            }
        attachments = collect_query_user_visuals(
            max_images=max_images,
            observation_visuals=visuals,
        )
        prompt = build_query_user_prompt(
            question,
            attachments,
            candidate_refs=candidate_refs,
        )
        future: concurrent.futures.Future[str] = concurrent.futures.Future()
        session.pending_query = future
        try:
            asyncio.run_coroutine_threadsafe(
                send_payload(open_id, prompt, attachments),
                main_loop,
            ).result(timeout=30)
        except Exception as exc:
            if session.pending_query is future:
                session.pending_query = None
            return {
                "success": False,
                "kind": "query_user_send_failed",
                "waited_ms": 0,
                "reason": f"failed to send question to user: {exc}",
            }

        wait_started = time.monotonic()
        try:
            answer = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            return {
                "success": False,
                "kind": "query_user_timeout",
                "waited_ms": int((time.monotonic() - wait_started) * 1000),
                "reason": f"user did not respond within {timeout_sec} sec",
            }
        finally:
            if session.pending_query is future:
                session.pending_query = None
        waited_ms = int((time.monotonic() - wait_started) * 1000)
        if answer == cancel_sentinel:
            return {
                "success": False,
                "kind": "query_user_cancelled",
                "waited_ms": waited_ms,
                "reason": "user cancelled",
            }
        return {
            "success": True,
            "kind": "query_user_answered",
            "waited_ms": waited_ms,
            "answer": answer,
        }

    session.harness.registry.replace(
        BuiltinTool(
            name="query_user",
            description=schema.get("description", ""),
            input_schema=schema.get("inputSchema", {}),
            fn=query_user_via_lark,
        )
    )


def install_notify_user(
    session: Any,
    open_id: str,
    main_loop: asyncio.AbstractEventLoop,
    *,
    client_factory: ClientFactory,
) -> None:
    """Replace the fallback ``notify_user`` with a one-way Lark adapter."""
    schema = next(
        (
            item
            for item in session.harness.registry.list_tool_definitions()
            if item.get("name") == "notify_user"
        ),
        None,
    )
    if schema is None:
        return

    def notify_user_via_lark(
        message: str = "",
        notification_type: str = "progress",
    ) -> dict[str, Any]:
        message = str(message or "").strip()
        if not message:
            return {
                "success": False,
                "kind": "notify_user_send_failed",
                "reason": "missing required argument 'message'",
            }
        if notification_type not in {"progress", "warning", "completion"}:
            return {
                "success": False,
                "kind": "notify_user_send_failed",
                "reason": f"unsupported notification_type: {notification_type}",
            }
        prefix = {"progress": "🔔", "warning": "⚠️", "completion": "✅"}
        try:
            sent = asyncio.run_coroutine_threadsafe(
                client_factory().send_text(
                    open_id,
                    f"{prefix[notification_type]} {message}",
                ),
                main_loop,
            ).result(timeout=30)
        except Exception as exc:
            return {
                "success": False,
                "kind": "notify_user_send_failed",
                "reason": f"failed to send notification to user: {exc}",
            }
        return {
            "success": True,
            "kind": "notify_user_sent",
            "notification_type": notification_type,
            "message_id": (sent.get("data") or {}).get("message_id", ""),
        }

    session.harness.registry.replace(
        BuiltinTool(
            name="notify_user",
            description=schema.get("description", ""),
            input_schema=schema.get("inputSchema", {}),
            fn=notify_user_via_lark,
        )
    )
