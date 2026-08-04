"""OpenAI-compatible boundary for context, Tool Calls, and Model Responses."""

from __future__ import annotations

import json
from typing import Any

from harness.context import ModelResponse, ToolCall
from harness.models.adapter_common import (
    OMIT_CONTENT_BLOCK,
    convert_content_blocks,
    mcp_input_schema,
)


def _openai_image_url_block(block: dict[str, Any]) -> dict[str, Any] | object:
    image_url = block.get("image_url")
    if not isinstance(image_url, dict):
        return OMIT_CONTENT_BLOCK
    url = image_url.get("url")
    if not isinstance(url, str) or not url:
        return OMIT_CONTENT_BLOCK
    normalized_url: dict[str, Any] = {"url": url}
    detail = image_url.get("detail")
    if isinstance(detail, str) and detail:
        normalized_url["detail"] = detail
    return {"type": "image_url", "image_url": normalized_url}


def _openai_image_source_block(
    block: dict[str, Any],
) -> dict[str, Any] | object:
    source = block.get("source")
    if not isinstance(source, dict):
        return OMIT_CONTENT_BLOCK
    source_type = source.get("type")
    value = source.get("data") if source_type == "base64" else source.get("url")
    if not isinstance(value, str) or not value:
        return OMIT_CONTENT_BLOCK
    if source_type == "base64":
        media_type = str(source.get("media_type") or "image/jpeg")
        value = f"data:{media_type};base64,{value}"
    elif source_type != "url":
        return OMIT_CONTENT_BLOCK
    return {"type": "image_url", "image_url": {"url": value}}


def _content_block_to_openai(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text":
        return {"type": "text", "text": str(block.get("text") or "")}
    if block_type == "image_url":
        return _openai_image_url_block(block)
    if block_type == "image":
        return _openai_image_source_block(block)
    return block


def _content_blocks_to_openai(blocks: list[Any]) -> list[Any]:
    return convert_content_blocks(blocks, _content_block_to_openai)


def mcp_tools_to_openai(
    mcp_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert MCP Tool definitions to OpenAI function Tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description") or "",
                "parameters": mcp_input_schema(tool),
            },
        }
        for tool in mcp_tools
    ]


def _tool_result_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {
        "role": "tool",
        "tool_call_id": message.get("tool_call_id", ""),
        "content": content,
    }


def _normalized_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = dict(tool_call.get("function", {}))
    if "arguments" in function and not isinstance(function["arguments"], str):
        function["arguments"] = json.dumps(function["arguments"], ensure_ascii=False)
    return {
        "id": tool_call.get("id", ""),
        "type": "function",
        "function": function,
    }


def _assistant_message(
    message: dict[str, Any],
    *,
    include_reasoning_content: bool,
) -> dict[str, Any]:
    content = message.get("content")
    entry: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) and content else None,
    }
    reasoning = message.get("reasoning_content")
    if include_reasoning_content and isinstance(reasoning, str):
        entry["reasoning_content"] = reasoning
    tool_calls = message.get("tool_calls", []) or []
    if tool_calls:
        entry["tool_calls"] = [
            _normalized_tool_call(tool_call) for tool_call in tool_calls
        ]
    return entry


def _plain_message(message: dict[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    content = message.get("content")
    if role == "user" and isinstance(content, list):
        content = _content_blocks_to_openai(content)
    elif isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False)
    return {"role": role, "content": content}


def _message_to_openai(
    message: dict[str, Any],
    *,
    include_reasoning_content: bool,
) -> dict[str, Any]:
    role = message.get("role")
    if role == "tool":
        return _tool_result_message(message)
    if role == "assistant":
        return _assistant_message(
            message,
            include_reasoning_content=include_reasoning_content,
        )
    return _plain_message(message)


def context_to_openai_messages(
    messages: list[dict[str, Any]],
    *,
    include_reasoning_content: bool = False,
    collapse_completed_tool_calls: bool = False,
) -> list[dict[str, Any]]:
    """Convert provider-neutral context messages to OpenAI messages.

    Tool Results are serialized as strings, Tool Call arguments are serialized
    as JSON, and provider-specific reasoning content remains opt-in.
    Completed Tool Call and Tool Result history can be collapsed for stateful
    OpenAI-compatible proxies that reject inactive historical call IDs.
    """
    if collapse_completed_tool_calls:
        messages = _collapse_completed_tool_call_messages(messages)

    return [
        _message_to_openai(
            message,
            include_reasoning_content=include_reasoning_content,
        )
        for message in messages
    ]


def _collapse_completed_tool_call_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert completed Tool Call and Tool Result history to plain messages."""
    collapsed_messages: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}

    for message in messages:
        tool_calls = message.get("tool_calls") or []
        if message.get("role") == "assistant" and tool_calls:
            collapsed_messages.extend(
                _collapsed_assistant_history(message, tool_calls, pending)
            )
        elif message.get("role") == "tool":
            collapsed_messages.append(_collapsed_tool_result_history(message, pending))
        else:
            collapsed_messages.append(dict(message))

    return collapsed_messages


def _collapsed_assistant_history(
    message: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    pending: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        messages.append({"role": "assistant", "content": content})
    for tool_call in tool_calls:
        call_id = str(tool_call.get("id") or "")
        if call_id:
            pending[call_id] = tool_call
        messages.append(
            {
                "role": "assistant",
                "content": _tool_call_history_text(tool_call),
            }
        )
    return messages


def _collapsed_tool_result_history(
    message: dict[str, Any],
    pending: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    call_id = str(message.get("tool_call_id") or "")
    tool_call = pending.pop(call_id, None)
    return {
        "role": "user",
        "content": _tool_result_history_text(
            tool_call,
            call_id,
            message.get("content"),
        ),
    }


def _tool_call_history_text(tool_call: dict[str, Any]) -> str:
    function = dict(tool_call.get("function") or {})
    name = str(function.get("name") or "unknown_tool")
    arguments = function.get("arguments", "{}")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return f"Previous tool call: {name}\nArguments: {arguments}"


def _tool_result_history_text(
    tool_call: dict[str, Any] | None,
    call_id: str,
    content: Any,
) -> str:
    name = "unknown_tool"
    if tool_call is not None:
        function = dict(tool_call.get("function") or {})
        name = str(function.get("name") or name)
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return f"Previous tool result for {name} ({call_id}):\n{content}"


def openai_response_to_model_response(resp: Any) -> ModelResponse:
    """Convert an OpenAI-compatible provider response to a Model Response."""
    choices = getattr(resp, "choices", None)
    if not choices:
        raise ValueError("OpenAI-compatible response has no choices")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise ValueError("OpenAI-compatible response choice has no message")
    text = message.content or ""
    if not isinstance(text, str):
        raise ValueError("OpenAI-compatible text content must be a string")
    reasoning_content = getattr(message, "reasoning_content", "") or ""
    if not isinstance(reasoning_content, str):
        raise ValueError(
            "OpenAI-compatible reasoning_content must be a string when present"
        )
    tool_calls: list[ToolCall] = []
    for provider_tool_call in getattr(message, "tool_calls", None) or []:
        function = provider_tool_call.function
        try:
            arguments = json.loads(function.arguments) if function.arguments else {}
        except json.JSONDecodeError:
            arguments = {"_raw_arguments": function.arguments}
        tool_calls.append(
            ToolCall(
                id=str(provider_tool_call.id or ""),
                name=str(function.name or ""),
                arguments=(
                    arguments if isinstance(arguments, dict) else {"_value": arguments}
                ),
            )
        )
    return ModelResponse(
        text=text,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        stop_reason=str(getattr(choice, "finish_reason", "") or ""),
    )


__all__ = [
    "context_to_openai_messages",
    "mcp_tools_to_openai",
    "openai_response_to_model_response",
]
