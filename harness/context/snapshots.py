"""Payload-safe model-context snapshot helpers.

This module formats a provider-neutral observability view of model context.
Provider adapters may transform the request further, so the snapshot is not a
claim about the exact provider-native payload. Consumers such as dashboards
and JSONL loggers may render the event, but snapshot construction never
influences tool selection or robot state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PROMPT_SNAPSHOT_SCHEMA_VERSION = 7

TOP_LEVEL_PROMPT_SECTIONS = {
    "System Prompt",
    "Memory",
    "Embodiment Profile",
    "Skill Catalog",
    "Scene Graph Brief",
    "Loaded Skill",
}

LOADED_SKILL_CONTEXT_SECTIONS = {
    "Loaded Skill",
}

RESIDENT_CONTEXT_SECTIONS = {
    "System Prompt",
    "Memory",
    "Embodiment Profile",
    "Skill Catalog",
}

REFRESHED_CONTEXT_SECTIONS = {
    "Scene Graph Brief",
}


def build_prompt_snapshot(
    *,
    turn: int,
    provider_system_content: str,
    tool_definitions: list[dict[str, Any]],
    accumulated_messages: list[dict[str, Any]],
    loaded_skill_context_blocks: list[dict[str, Any]],
    observation_messages: list[dict[str, Any]] | None = None,
    resident_context: str | None = None,
    refreshed_context: str | None = None,
    transient_tool_result_messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a segmented, payload-safe, provider-neutral context view."""
    sections = _segment_provider_system_content(provider_system_content)
    definition_snapshot = _snapshot_tool_definitions(tool_definitions)
    accumulated_message_snapshot = _snapshot_context_messages(accumulated_messages)
    transient_tool_result_messages = list(transient_tool_result_messages or [])
    transient_tool_result_snapshot = _snapshot_context_messages(
        transient_tool_result_messages
    )
    observation_messages = list(observation_messages or [])
    observation_message_snapshot = _snapshot_context_messages(observation_messages)
    model_message_snapshot = _snapshot_context_messages(
        accumulated_messages + transient_tool_result_messages + observation_messages
    )
    return {
        "type": "prompt_snapshot",
        "schema_version": PROMPT_SNAPSHOT_SCHEMA_VERSION,
        "turn": turn,
        "provider_system_content": provider_system_content,
        "section_count": len(sections),
        "sections": sections,
        "resident_sections": [
            section["name"]
            for section in sections
            if section.get("kind") == "resident_context"
        ],
        "loaded_skill_context": {
            "count": len(loaded_skill_context_blocks),
            "names": [
                str(block.get("name") or "")
                for block in loaded_skill_context_blocks
                if block.get("name")
            ],
            "blocks": loaded_skill_context_blocks,
        },
        "tools": {
            "count": len(tool_definitions),
            "names": [
                str(definition.get("name") or "")
                for definition in tool_definitions
                if definition.get("name")
            ],
        },
        "tool_definitions": definition_snapshot,
        "accumulated_messages": _summarize_context_messages(accumulated_messages),
        "accumulated_message_snapshot": accumulated_message_snapshot,
        "resident_context": _snapshot_text_context_layer(resident_context),
        "refreshed_context": _snapshot_text_context_layer(refreshed_context),
        "transient_tool_result_messages": _summarize_context_messages(
            transient_tool_result_messages
        ),
        "transient_tool_result_snapshot": (transient_tool_result_snapshot),
        "observation_messages": _summarize_context_messages(observation_messages),
        "observation_message_snapshot": observation_message_snapshot,
        "prompt_payload": {
            "system": provider_system_content,
            "tools": definition_snapshot,
            "messages": model_message_snapshot["items"],
        },
        "total_chars": (
            len(provider_system_content)
            + len(
                json.dumps(
                    definition_snapshot,
                    ensure_ascii=False,
                    default=str,
                )
            )
            + len(
                json.dumps(
                    model_message_snapshot["items"],
                    ensure_ascii=False,
                    default=str,
                )
            )
        ),
    }


def _segment_provider_system_content(content: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_name = "System Prompt"
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if not content and current_heading is None:
            return
        if not content and sections:
            return
        sections.append(
            {
                "name": current_name,
                "heading": current_heading,
                "kind": _prompt_section_kind(current_name),
                "content": content,
                "char_count": len(content),
            }
        )

    for line in str(content or "").splitlines():
        if line.startswith("## "):
            candidate_name = line[3:].strip() or "Untitled"
            if candidate_name in TOP_LEVEL_PROMPT_SECTIONS:
                flush()
                current_name = candidate_name
                current_heading = line.rstrip()
                current_lines = []
                continue
            current_lines.append(line)
            continue
        current_lines.append(line)
    flush()
    return sections


def _prompt_section_kind(name: str) -> str:
    if name in LOADED_SKILL_CONTEXT_SECTIONS:
        return "resident_context"
    if name in RESIDENT_CONTEXT_SECTIONS:
        return "resident_context"
    if name in REFRESHED_CONTEXT_SECTIONS:
        return "refreshed_context"
    return "other"


def _summarize_context_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    recent: list[dict[str, Any]] = []
    for message in messages[-8:]:
        content = message.get("content")
        if isinstance(content, str):
            content_type = "text"
            char_count = len(content)
        elif isinstance(content, list):
            content_type = "blocks"
            char_count = len(str(content))
        elif isinstance(content, dict):
            content_type = "object"
            char_count = len(str(content))
        else:
            content_type = type(content).__name__
            char_count = len(str(content or ""))
        recent.append(
            {
                "role": str(message.get("role") or ""),
                "content_type": content_type,
                "char_count": char_count,
                "tool_call_id": str(message.get("tool_call_id") or ""),
                "kind": _message_metadata_value(message, "kind"),
                "source": _message_metadata_value(message, "source"),
            }
        )
    return {
        "count": len(messages),
        "recent": recent,
    }


def _snapshot_context_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Capture model messages while replacing large image/base64 payloads."""
    items: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        content = message.get("content")
        item: dict[str, Any] = {
            "index": index,
            "role": str(message.get("role") or ""),
            "content_type": _content_type_label(content),
            "char_count": len(str(content or "")),
            "content": _prompt_snapshot_safe_value(content),
        }
        for key in (
            "name",
            "tool_call_id",
            "reasoning_content",
            "tool_calls",
            "kind",
            "source",
            "metadata",
        ):
            if key in message:
                item[key] = _prompt_snapshot_safe_value(message.get(key))
        items.append(item)
    return {
        "count": len(messages),
        "items": items,
    }


def _snapshot_tool_definitions(
    definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for definition in definitions:
        if isinstance(definition, dict):
            snapshot = _prompt_snapshot_safe_value(definition)
            if isinstance(snapshot, dict):
                snapshots.append(snapshot)
    return snapshots


def _snapshot_text_context_layer(content: str | None) -> dict[str, Any]:
    value = str(content or "")
    return {
        "provided": content is not None,
        "char_count": len(value),
        "content": _prompt_snapshot_safe_value(value),
        "sections": _segment_provider_system_content(value),
    }


def _message_metadata_value(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if value in (None, ""):
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get(key)
    return str(value or "").strip()


def _content_type_label(content: Any) -> str:
    if isinstance(content, str):
        return "text"
    if isinstance(content, list):
        return "blocks"
    if isinstance(content, dict):
        return "object"
    return type(content).__name__


def _prompt_snapshot_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return _binary_payload_summary(value)
    if isinstance(value, str):
        data_url_summary = _data_image_url_summary(value)
        return data_url_summary if data_url_summary is not None else value
    if isinstance(value, tuple):
        return [_prompt_snapshot_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_prompt_snapshot_safe_value(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        is_base64_source = value.get("type") == "base64" and isinstance(
            value.get("data"), str
        )
        for key, item in value.items():
            if is_base64_source and key == "data":
                out[key] = _base64_payload_summary(
                    item,
                    media_type=str(value.get("media_type") or ""),
                )
            else:
                out[str(key)] = _prompt_snapshot_safe_value(item)
        return out
    return str(value)


def _data_image_url_summary(value: str) -> dict[str, Any] | None:
    prefix, separator, data = value.partition(",")
    if (
        separator != ","
        or not prefix.startswith("data:image/")
        or ";base64" not in prefix
    ):
        return None
    media_type = prefix.removeprefix("data:").split(";", 1)[0]
    return {
        "type": "image_data_url",
        "media_type": media_type,
        "char_count": len(value),
        "data_char_count": len(data),
        "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
    }


def _base64_payload_summary(value: str, *, media_type: str = "") -> dict[str, Any]:
    return {
        "type": "base64_payload",
        "media_type": media_type,
        "char_count": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _binary_payload_summary(value: bytes) -> dict[str, Any]:
    return {
        "type": "bytes",
        "byte_count": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
    }


__all__ = ["PROMPT_SNAPSHOT_SCHEMA_VERSION", "build_prompt_snapshot"]
