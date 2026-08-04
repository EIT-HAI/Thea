"""Presentation primitives for Lark cards and Harness event traces.

This module converts channel-neutral Harness events into concise Lark card
content. It does not send messages or access session state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

LARK_CARD_MD_CHUNK_CHARS = 6_000
LARK_LIVE_EVENT_LIMIT = 16


def _chunk_lark_markdown(
    text: str,
    *,
    max_chars: int = LARK_CARD_MD_CHUNK_CHARS,
) -> list[str]:
    """Split card Markdown into complete elements without dropping content."""
    remaining = str(text or "")
    if not remaining:
        return []
    chunks: list[str] = []
    while len(remaining) > max_chars:
        cut = remaining.rfind("\n", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        else:
            cut += 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _lark_md_elements(text: str) -> list[dict[str, Any]]:
    """Render Markdown as bounded Lark card elements."""
    return [
        {"tag": "div", "text": {"tag": "lark_md", "content": chunk}}
        for chunk in _chunk_lark_markdown(text)
    ]


def _thinking_card(
    turn: int,
    last_event: str = "",
    live_events: list[str] | None = None,
) -> dict[str, Any]:
    """Render the live progress card for one Harness run."""
    if live_events:
        visible_events = live_events[-LARK_LIVE_EVENT_LIMIT:]
        hidden_count = len(live_events) - len(visible_events)
        prefix = (
            f"_{hidden_count} earlier events remain available in the structured run log._\n"
            if hidden_count > 0
            else ""
        )
        body = prefix + "\n".join(visible_events)
    else:
        body = f"**Turn {turn}** · {last_event}" if last_event else "_planning..._"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🤖 Thea Agent · Processing"},
            "template": "blue",
        },
        "elements": _lark_md_elements(body),
    }


@dataclass
class _RunTimingStats:
    """Aggregate per-step durations emitted by ``Harness.run_stream``."""

    model_total_ms: int = 0
    model_calls: int = 0
    tool_total_ms: int = 0
    tool_calls: int = 0
    tool_details: list[dict[str, Any]] = field(default_factory=list)
    user_wait_total_ms: int = 0
    user_wait_calls: int = 0
    user_wait_details: list[dict[str, Any]] = field(default_factory=list)
    lark_total_ms: int = 0
    lark_calls: int = 0
    lark_details: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: dict[str, Any]) -> None:
        """Record one duration-bearing Harness event."""
        duration = event.get("duration_ms")
        if duration is None:
            return
        try:
            duration_ms = max(0, int(duration))
        except (TypeError, ValueError):
            return

        event_type = event.get("type")
        if event_type == "model_response":
            self.model_total_ms += duration_ms
            self.model_calls += 1
        elif event_type == "tool_result":
            if event.get("name") == "query_user":
                result = (
                    event.get("result") if isinstance(event.get("result"), dict) else {}
                )
                try:
                    duration_ms = max(0, int(result.get("waited_ms", duration_ms)))
                except (TypeError, ValueError):
                    duration_ms = 0
                self.user_wait_total_ms += duration_ms
                self.user_wait_calls += 1
                self.user_wait_details.append(
                    {
                        "name": event.get("name") or "?",
                        "duration_ms": duration_ms,
                        "success": bool(event.get("success", False)),
                        "kind": result.get("kind", ""),
                    }
                )
                return
            self.tool_total_ms += duration_ms
            self.tool_calls += 1
            self.tool_details.append(
                {
                    "name": event.get("name") or "?",
                    "duration_ms": duration_ms,
                    "success": bool(event.get("success", False)),
                }
            )

    def record_lark(self, name: str, duration_ms: int | float) -> None:
        """Record one Lark API operation."""
        duration = max(0, int(duration_ms))
        self.lark_total_ms += duration
        self.lark_calls += 1
        self.lark_details.append({"name": name, "duration_ms": duration})

    def as_dict(self, elapsed_ms: int | None = None) -> dict[str, Any]:
        """Return JSON-ready timing totals and optional unclassified time."""
        known_ms = (
            self.model_total_ms
            + self.tool_total_ms
            + self.user_wait_total_ms
            + self.lark_total_ms
        )
        data: dict[str, Any] = {
            "model_total_ms": self.model_total_ms,
            "model_calls": self.model_calls,
            "tool_total_ms": self.tool_total_ms,
            "tool_calls": self.tool_calls,
            "tool_details": list(self.tool_details),
            "user_wait_total_ms": self.user_wait_total_ms,
            "user_wait_calls": self.user_wait_calls,
            "user_wait_details": list(self.user_wait_details),
            "lark_total_ms": self.lark_total_ms,
            "lark_calls": self.lark_calls,
            "lark_details": list(self.lark_details),
        }
        if elapsed_ms is not None:
            data["elapsed_ms"] = elapsed_ms
            data["other_ms"] = max(0, elapsed_ms - known_ms)
        return data


def _final_card(
    text: str,
    *,
    ok: bool = True,
    image_blocks: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Render only the user-facing final answer and its output images."""
    elements: list[dict[str, Any]] = _lark_md_elements(text)
    for image in image_blocks or []:
        image_key = image.get("img_key", "")
        if not image_key:
            continue
        elements.append(
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {
                    "tag": "plain_text",
                    "content": image.get("alt") or "Robot image",
                },
                "mode": "fit_horizontal",
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "✅ Thea Agent" if ok else "⚠️ Thea Agent",
            },
            "template": "green" if ok else "red",
        },
        "elements": elements,
    }


def _model_response_event_brief(event: dict[str, Any]) -> str:
    reasoning = str(event.get("reasoning_content") or "").strip()
    decision = str(event.get("decision_summary") or "").strip()
    parts: list[str] = []
    if reasoning:
        parts.append(f"💭 **Think**\n{reasoning}")
    if decision:
        parts.append(f"🧭 **Decision**\n{decision}")
    return "\n".join(parts) if parts else "💭 thinking..."


def _speech_event_brief(event: dict[str, Any]) -> str:
    text = (event.get("text") or "").strip().replace("\n", " ")
    return f"🗣 {text}" if text else ""


def _skill_loaded_event_brief(event: dict[str, Any]) -> str:
    name = str(event.get("name") or "").strip()
    return f"🧩 Loaded skill `{name}` for this task" if name else ""


def _tool_call_event_brief(event: dict[str, Any]) -> str:
    if event.get("name") == "query_user":
        question = str((event.get("arguments") or {}).get("question") or "").strip()
        if question:
            return f"❓ Awaiting your confirmation: {question}"
    if event.get("name") == "notify_user":
        message = str((event.get("arguments") or {}).get("message") or "").strip()
        return f"🔔 User notification: {message}" if message else "🔔 User notification"
    args_str = json.dumps(event.get("arguments") or {}, ensure_ascii=False)
    return f"🔧 `{event.get('name', '?')}({args_str})`"


def _tool_result_event_brief(event: dict[str, Any]) -> str:
    result = event.get("result") or {}
    if event.get("name") == "notify_user" and result.get("kind") == "notify_user_sent":
        return f"✓ Notification sent ({result.get('notification_type', 'progress')})"
    observation = result.get("observation") or result.get("reason", "?")
    mark = "✓" if event.get("success") else "✗"
    image_suffix = " 🖼" if _extract_visual_output_urls(result) else ""
    return f"{mark} {str(observation)}{image_suffix}"


def _replan_event_brief(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip().replace("\n", " ")
    return f"↻ Current skill completed; replanning from revised instruction: {text}"


def _event_brief(event: dict[str, Any]) -> str:
    """Condense a stream event into a human-readable card status."""
    formatter = {
        "model_response": _model_response_event_brief,
        "say": _speech_event_brief,
        "speak": _speech_event_brief,
        "skill_loaded": _skill_loaded_event_brief,
        "tool_call": _tool_call_event_brief,
        "tool_result": _tool_result_event_brief,
        "replan_requested": _replan_event_brief,
    }.get(event.get("type", ""))
    return formatter(event) if formatter is not None else ""


def _extract_visual_output_urls(result: dict[str, Any]) -> list[str]:
    """Collect URL-backed visual outputs from one Tool Result."""
    outputs = result.get("visual_outputs")
    if not isinstance(outputs, list):
        return []
    urls: list[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url")
        if isinstance(image_url, dict) and image_url.get("url"):
            urls.append(str(image_url["url"]))
        elif item.get("url"):
            urls.append(str(item["url"]))
    return urls


def _clean_final_text_for_lark(text: str) -> str:
    """Remove transport-only visual markup from the user-facing answer."""
    cleaned = re.sub(
        r"\n*<visual_outputs>.*?</visual_outputs>\n*",
        "\n\n",
        text or "",
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"\n*!\[[^\]]*\]\([^)]+\)\n*", "\n\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or "Task completed."


__all__ = []
