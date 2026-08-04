"""Shared content and Tool Definition helpers for model-provider adapters."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

OMIT_CONTENT_BLOCK = object()


def parse_data_image_url(url: str) -> tuple[str, str] | None:
    """Return the media type and payload from a base64 image data URL."""
    prefix, separator, data = url.partition(",")
    if separator != "," or not prefix.startswith("data:image/"):
        return None
    metadata = prefix.removeprefix("data:")
    if ";base64" not in metadata:
        return None
    media_type = metadata.split(";", 1)[0]
    if not data:
        return None
    return media_type, data


def convert_content_blocks(
    blocks: list[Any],
    converter: Callable[[Any], Any],
) -> list[Any]:
    """Convert Observation content blocks while preserving image captions."""
    converted_blocks: list[Any] = []
    for source_block in blocks:
        converted = converter(source_block)
        if converted is OMIT_CONTENT_BLOCK:
            continue
        caption = image_caption(source_block)
        if caption:
            converted_blocks.append(
                {"type": "text", "text": f"Image source: {caption}"}
            )
        converted_blocks.append(converted)
    return converted_blocks


def image_caption(block: Any) -> str:
    """Return a normalized caption for an Observation image block."""
    if not isinstance(block, dict) or block.get("type") not in {
        "image",
        "image_url",
    }:
        return ""
    caption = block.get("caption")
    return str(caption).strip() if caption is not None else ""


def mcp_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """Copy and normalize one MCP Tool input schema for a provider."""
    raw_schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    schema = deepcopy(dict(raw_schema))
    schema.pop("title", None)
    for prop in (schema.get("properties") or {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return schema


__all__ = [
    "OMIT_CONTENT_BLOCK",
    "convert_content_blocks",
    "image_caption",
    "mcp_input_schema",
    "parse_data_image_url",
]
