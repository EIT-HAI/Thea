"""Anthropic boundary for context, Tool Calls, and Model Responses."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from harness.context import ModelResponse, ToolCall
from harness.models.adapter_common import (
    OMIT_CONTENT_BLOCK,
    convert_content_blocks,
    mcp_input_schema,
    parse_data_image_url,
)


def _anthropic_image_block(block: dict[str, Any]) -> dict[str, Any] | object:
    source = block.get("source")
    if not isinstance(source, dict):
        return OMIT_CONTENT_BLOCK
    return {"type": "image", "source": deepcopy(source)}


def _anthropic_image_url_block(
    block: dict[str, Any],
) -> dict[str, Any] | object:
    image_url = block.get("image_url")
    if not isinstance(image_url, dict):
        return OMIT_CONTENT_BLOCK
    url = image_url.get("url")
    if not isinstance(url, str) or not url:
        return OMIT_CONTENT_BLOCK
    parsed = parse_data_image_url(url)
    if parsed is None:
        return {"type": "image", "source": {"type": "url", "url": url}}
    media_type, data = parsed
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _content_block_to_anthropic(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text":
        return {"type": "text", "text": str(block.get("text") or "")}
    if block_type == "image":
        return _anthropic_image_block(block)
    if block_type == "image_url":
        return _anthropic_image_url_block(block)
    return block


def _content_blocks_to_anthropic(blocks: list[Any]) -> list[Any]:
    return convert_content_blocks(blocks, _content_block_to_anthropic)


def mcp_tools_to_anthropic(
    mcp_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert MCP Tool definitions to Anthropic Tool definitions."""
    return [
        {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "input_schema": mcp_input_schema(tool),
        }
        for tool in mcp_tools
    ]


def _user_message(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = _content_blocks_to_anthropic(content)
    return {"role": "user", "content": content}


def _tool_result_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id", ""),
                "content": content,
            }
        ],
    }


def _tool_use_block(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function", {})
    arguments = function.get("arguments", "{}")
    try:
        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        arguments = {}
    return {
        "type": "tool_use",
        "id": tool_call.get("id", ""),
        "name": function.get("name", ""),
        "input": arguments or {},
    }


def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    blocks: list[Any] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    blocks.extend(
        _tool_use_block(tool_call) for tool_call in message.get("tool_calls", []) or []
    )
    return {
        "role": "assistant",
        "content": blocks or [{"type": "text", "text": ""}],
    }


def _message_to_anthropic(
    message: dict[str, Any],
) -> dict[str, Any] | None:
    role = message.get("role")
    if role == "system":
        return None
    if role == "user":
        return _user_message(message.get("content"))
    if role == "tool":
        return _tool_result_message(message)
    if role == "assistant":
        return _assistant_message(message)
    return None


def context_to_anthropic_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert provider-neutral context messages to Anthropic messages.

    The provider system field remains separate. Tool Results become user
    content blocks, and assistant Tool Calls become ``tool_use`` blocks.
    """
    converted_messages: list[dict[str, Any]] = []
    for message in messages:
        converted = _message_to_anthropic(message)
        if converted is not None:
            converted_messages.append(converted)
    return converted_messages


def anthropic_response_to_model_response(resp: Any) -> ModelResponse:
    """Convert an Anthropic provider response to a Model Response."""
    content = getattr(resp, "content", None)
    if not isinstance(content, list) or not content:
        raise ValueError("Anthropic response has no content blocks")

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", "") or ""
            if not isinstance(text, str):
                raise ValueError("Anthropic text content must be a string")
            text_parts.append(text)
        elif block_type == "tool_use":
            arguments = getattr(block, "input", {}) or {}
            tool_calls.append(
                ToolCall(
                    id=str(getattr(block, "id", "") or ""),
                    name=str(getattr(block, "name", "") or ""),
                    arguments=(
                        dict(arguments)
                        if isinstance(arguments, dict)
                        else {"_value": arguments}
                    ),
                )
            )
    return ModelResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=str(getattr(resp, "stop_reason", "") or ""),
    )


__all__ = [
    "anthropic_response_to_model_response",
    "context_to_anthropic_messages",
    "mcp_tools_to_anthropic",
]
