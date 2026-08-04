"""Accumulated-context compaction for model-visible messages."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harness.context import TASK_NOTES_MESSAGE_KIND, Context
from harness.context.redaction import redact_sensitive_data
from harness.runtime.task import abnormal_model_stop

COMPACTED_ACCUMULATED_MESSAGES_PREFIX = "Compacted Accumulated Messages"
HARNESS_POST_EXECUTION_HOOK_SOURCE = "harness_post_execution_hook"
COMPACTION_SUMMARY_MESSAGE_KIND = "compaction_summary"
HARNESS_COMPACTION_SOURCE = "harness_compaction"

_COMPACTION_SYSTEM_PROMPT = """You create a context checkpoint for a robot agent.
Summarize only the Accumulated messages supplied to you. Newer turns remain
verbatim outside the checkpoint.

Return concise Markdown with exactly these sections:
## Objective and Constraints
## Completed and In Progress
## Tool Evidence
## Open Work

Preserve user instructions, corrections, preferences, explicit constraints,
confirmed object refs, identifiers, parameters, important Tool Results,
failures, and recovery evidence. When a previous checkpoint is provided,
update it: retain facts that still matter, remove stale claims, and merge the
new messages. Historical visual evidence and Tool Results must remain clearly
historical because a fresh Observation and Scene Graph Brief are supplied
separately. Do not invent facts, answer the task, or mention compaction.
Use the language of the supplied conversation."""

_REQUIRED_SUMMARY_HEADINGS = (
    "## Objective and Constraints",
    "## Completed and In Progress",
    "## Tool Evidence",
    "## Open Work",
)


@dataclass(frozen=True)
class CompactionConfig:
    """Budgets controlling old-prefix compaction of Accumulated context."""

    enabled: bool = True
    context_window: int = 200_000
    reserve_tokens: int = 16_384
    keep_recent_turns: int = 4
    keep_recent_tokens: int = 20_000
    tool_result_max_chars: int = 2_000
    reasoning_max_chars: int = 2_000
    max_input_chars: int = 400_000
    max_summary_rounds: int = 8

    @property
    def token_threshold(self) -> int:
        """Return the input budget after reserving room for the next response."""
        return max(1, self.context_window - self.reserve_tokens)

    @classmethod
    def from_root_config(cls, root: dict[str, Any]) -> CompactionConfig:
        context_config = root.get("context")
        raw: dict[str, Any] = {}
        if isinstance(context_config, dict):
            candidate = context_config.get("compaction")
            if isinstance(candidate, dict):
                raw = candidate

        llm_config = root.get("llm")
        provider = ""
        if isinstance(llm_config, dict):
            provider = str(llm_config.get("provider") or "").strip().lower()

        enabled_default = provider != "mock"
        return cls(
            enabled=_as_bool(raw.get("enabled"), default=enabled_default),
            context_window=max(
                2,
                _as_int(raw.get("context_window"), 200_000),
            ),
            reserve_tokens=max(
                1,
                _as_int(raw.get("reserve_tokens"), 16_384),
            ),
            keep_recent_turns=max(1, _as_int(raw.get("keep_recent_turns"), 4)),
            keep_recent_tokens=max(
                1,
                _as_int(raw.get("keep_recent_tokens"), 20_000),
            ),
            tool_result_max_chars=max(
                1,
                _as_int(raw.get("tool_result_max_chars"), 2_000),
            ),
            reasoning_max_chars=max(
                1,
                _as_int(raw.get("reasoning_max_chars"), 2_000),
            ),
            max_input_chars=max(
                10_000,
                _as_int(raw.get("max_input_chars"), 400_000),
            ),
            max_summary_rounds=max(
                1,
                _as_int(raw.get("max_summary_rounds"), 8),
            ),
        )


class ContextCompactor:
    """Explicit, observable compaction of Accumulated context."""

    def __init__(
        self,
        config: CompactionConfig,
        *,
        sanitizer: Callable[[Any], Any] | None = redact_sensitive_data,
    ):
        self.config = config
        self.sanitizer = sanitizer
        self.consecutive_failures = 0
        self.compaction_count = 0

    @classmethod
    def from_config(
        cls,
        root: dict[str, Any],
        *,
        sanitizer: Callable[[Any], Any] | None = redact_sensitive_data,
    ) -> ContextCompactor:
        return cls(
            CompactionConfig.from_root_config(root),
            sanitizer=sanitizer,
        )

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.compaction_count = 0

    def maybe_compact(
        self,
        context: Context,
        model: Any,
    ) -> dict[str, Any] | None:
        """Compact an old Accumulated-message prefix near the context limit."""
        if not self.config.enabled:
            return None

        before_tokens = context.estimate_token_count()
        if before_tokens < self.config.token_threshold:
            return None

        boundary = _recent_turn_boundary(
            context.accumulated_messages,
            keep_recent_turns=self.config.keep_recent_turns,
            keep_recent_tokens=self.config.keep_recent_tokens,
        )
        if boundary is None:
            return None

        old_messages = list(context.accumulated_messages[:boundary])
        recent_messages = list(context.accumulated_messages[boundary:])
        if not old_messages or not recent_messages:
            return None

        before_accumulated_tokens = _estimate_message_tokens(
            context.accumulated_messages
        )
        started = time.monotonic()
        try:
            summary, summary_rounds = _request_compaction_summary(
                model,
                old_messages,
                sanitizer=self.sanitizer,
                tool_result_max_chars=self.config.tool_result_max_chars,
                reasoning_max_chars=self.config.reasoning_max_chars,
                max_input_chars=self.config.max_input_chars,
                max_summary_rounds=self.config.max_summary_rounds,
            )
            replacement = {
                "role": "user",
                "content": (f"{COMPACTED_ACCUMULATED_MESSAGES_PREFIX}\n\n{summary}"),
                "kind": COMPACTION_SUMMARY_MESSAGE_KIND,
                "source": HARNESS_COMPACTION_SOURCE,
            }
            original_messages = list(context.accumulated_messages)
            context.replace_accumulated_messages([replacement, *recent_messages])
            after_tokens = context.estimate_token_count()
            after_accumulated_tokens = _estimate_message_tokens(
                context.accumulated_messages
            )
            if after_accumulated_tokens >= before_accumulated_tokens:
                context.replace_accumulated_messages(original_messages)
                raise ValueError(
                    "compaction did not reduce Accumulated context",
                )
            self.consecutive_failures = 0
            self.compaction_count += 1
            return {
                "type": "context_compaction",
                "status": "completed",
                "before_estimated_tokens": before_tokens,
                "after_estimated_tokens": after_tokens,
                "accumulated_estimated_tokens_before": (before_accumulated_tokens),
                "accumulated_estimated_tokens_after": after_accumulated_tokens,
                "messages_summarized": len(old_messages),
                "messages_retained": len(recent_messages),
                "context_window": self.config.context_window,
                "reserve_tokens": self.config.reserve_tokens,
                "trigger_threshold": self.config.token_threshold,
                "keep_recent_turns": self.config.keep_recent_turns,
                "keep_recent_tokens": self.config.keep_recent_tokens,
                "summary_rounds": summary_rounds,
                "compaction_count": self.compaction_count,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        except Exception as exc:
            self.consecutive_failures += 1
            return {
                "type": "context_compaction",
                "status": "failed",
                "before_estimated_tokens": before_tokens,
                "accumulated_estimated_tokens_before": (before_accumulated_tokens),
                "messages_summarized": 0,
                "messages_retained": len(context.accumulated_messages),
                "consecutive_failures": self.consecutive_failures,
                "disabled": False,
                "error": f"{type(exc).__name__}: {exc}",
                "duration_ms": int((time.monotonic() - started) * 1000),
            }


def _recent_turn_boundary(
    messages: list[dict[str, Any]],
    *,
    keep_recent_turns: int,
    keep_recent_tokens: int,
) -> int | None:
    """Select a complete recent tail bounded by turns and estimated tokens."""
    model_response_indices = [
        index
        for index, message in enumerate(messages)
        if (
            message.get("role") == "assistant"
            and _message_metadata_value(message, "source")
            != HARNESS_POST_EXECUTION_HOOK_SOURCE
        )
    ]
    if not model_response_indices:
        return None

    tail_count = min(max(1, int(keep_recent_turns)), len(model_response_indices))
    tail_candidates = model_response_indices[-tail_count:]
    token_budget = max(1, int(keep_recent_tokens))

    for candidate in tail_candidates:
        boundary = _include_preceding_user_context(messages, candidate)
        if boundary <= 0:
            continue
        if _estimate_message_tokens(messages[boundary:]) <= token_budget:
            return boundary

    boundary = _include_preceding_user_context(
        messages,
        model_response_indices[-1],
    )
    return boundary if boundary > 0 else None


def _include_preceding_user_context(
    messages: list[dict[str, Any]],
    boundary: int,
) -> int:
    while boundary > 0 and messages[boundary - 1].get("role") == "user":
        boundary -= 1
    return boundary


def _request_compaction_summary(
    model: Any,
    messages: list[dict[str, Any]],
    *,
    sanitizer: Callable[[Any], Any] | None = redact_sensitive_data,
    tool_result_max_chars: int = 2_000,
    reasoning_max_chars: int = 2_000,
    max_input_chars: int = 400_000,
    max_summary_rounds: int = 8,
) -> tuple[str, int]:
    safe_messages = sanitizer(messages) if sanitizer is not None else messages
    if not isinstance(safe_messages, list):
        raise TypeError("compaction sanitizer must return a message list")
    previous_summary, messages_to_summarize = _extract_previous_checkpoint(
        safe_messages
    )
    segments = _render_compaction_segments(
        messages_to_summarize,
        tool_result_max_chars=tool_result_max_chars,
        reasoning_max_chars=reasoning_max_chars,
    )
    if not segments:
        raise ValueError("no eligible Accumulated messages to compact")

    input_limit = max(10_000, int(max_input_chars))
    fixed_chars = (
        len(_COMPACTION_SYSTEM_PROMPT)
        + len(previous_summary)
        + len("<previous-checkpoint></previous-checkpoint>")
        + len("<new-accumulated-messages></new-accumulated-messages>")
        + 1_000
    )
    chunk_limit = max(2_000, int(input_limit * 0.8) - fixed_chars)
    chunks = _chunk_compaction_segments(segments, max_chars=chunk_limit)
    round_limit = max(1, int(max_summary_rounds))
    if len(chunks) > round_limit:
        raise ValueError(
            "compaction requires "
            f"{len(chunks)} summary rounds, exceeding max_summary_rounds="
            f"{round_limit}"
        )

    summary = previous_summary
    for index, chunk in enumerate(chunks, start=1):
        summary = _request_compaction_round(
            model,
            chunk,
            previous_summary=summary,
            max_input_chars=input_limit,
            round_index=index,
            round_count=len(chunks),
        )
    return summary, len(chunks)


def _extract_previous_checkpoint(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Use an earlier Harness checkpoint as the rolling-summary anchor."""
    checkpoint = ""
    retained: list[dict[str, Any]] = []
    for message in messages:
        is_checkpoint = (
            _message_metadata_value(message, "kind") == COMPACTION_SUMMARY_MESSAGE_KIND
            and _message_metadata_value(message, "source") == HARNESS_COMPACTION_SOURCE
        )
        if not is_checkpoint:
            retained.append(message)
            continue
        content = str(message.get("content") or "").strip()
        prefix = COMPACTED_ACCUMULATED_MESSAGES_PREFIX
        if content.startswith(prefix):
            content = content[len(prefix) :].lstrip()
        if content:
            checkpoint = content
    return checkpoint, retained


def _request_compaction_round(
    model: Any,
    transcript: str,
    *,
    previous_summary: str,
    max_input_chars: int,
    round_index: int,
    round_count: int,
) -> str:
    parts = [
        (
            f"Checkpoint update {round_index} of {round_count}. "
            "Merge the new messages into one continuation checkpoint."
        )
    ]
    if previous_summary:
        parts.append(
            "<previous-checkpoint>\n" + previous_summary + "\n</previous-checkpoint>"
        )
    parts.append(
        "<new-accumulated-messages>\n" + transcript + "\n</new-accumulated-messages>"
    )
    prompt = "\n\n".join(parts)
    request_chars = len(_COMPACTION_SYSTEM_PROMPT) + len(prompt)
    if request_chars > max_input_chars:
        raise ValueError(
            "compaction summary request exceeds max_input_chars "
            f"({request_chars} > {max_input_chars})"
        )

    summary_context = Context()
    summary_context.set_context_layers(resident=_COMPACTION_SYSTEM_PROMPT)
    summary_context.add_user_message(prompt)
    response = model.call(summary_context, tools=[])
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    stop = abnormal_model_stop(
        str(getattr(response, "stop_reason", "") or ""),
        has_tool_calls=bool(tool_calls),
    )
    if stop is not None:
        raise ValueError(
            f"compaction model returned an incomplete response ({stop.stop_reason})"
        )
    text = str(getattr(response, "text", "") or "").strip()
    if tool_calls:
        raise ValueError("compaction model returned tool calls")
    if not text:
        raise ValueError("compaction model returned an empty summary")
    _validate_summary(text)
    return text


def _chunk_compaction_segments(
    segments: list[str],
    *,
    max_chars: int,
) -> list[str]:
    """Partition all serialized history without silently dropping its middle."""
    limit = max(1_000, int(max_chars))
    expanded: list[str] = []
    for segment in segments:
        if len(segment) <= limit:
            expanded.append(segment)
            continue
        marker_reserve = len("[Oversized message part 9999/9999]\n")
        payload_limit = max(1, limit - marker_reserve)
        parts = [
            segment[start : start + payload_limit]
            for start in range(0, len(segment), payload_limit)
        ]
        for index, part in enumerate(parts, start=1):
            expanded.append(f"[Oversized message part {index}/{len(parts)}]\n{part}")

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for segment in expanded:
        separator_chars = 2 if current else 0
        if current and current_chars + separator_chars + len(segment) > limit:
            chunks.append("\n\n".join(current))
            current = []
            current_chars = 0
            separator_chars = 0
        current.append(segment)
        current_chars += separator_chars + len(segment)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _render_compaction_segments(
    messages: list[dict[str, Any]],
    *,
    tool_result_max_chars: int = 2_000,
    reasoning_max_chars: int = 2_000,
) -> list[str]:
    rendered: list[str] = []
    for index, message in enumerate(messages, start=1):
        if _message_metadata_value(message, "kind") == TASK_NOTES_MESSAGE_KIND:
            continue
        parts: list[str] = []
        content = message.get("content")
        role = str(message.get("role") or "unknown").upper()
        header = f"[{index:04d}] {role}"
        if message.get("tool_call_id"):
            header += f" tool_call_id={message['tool_call_id']}"
        parts.append(header)

        if content is not None:
            rendered_content = _render_value(content)
            if role == "TOOL":
                rendered_content = _truncate_for_summary(
                    rendered_content,
                    max_chars=tool_result_max_chars,
                    label="tool result",
                )
            parts.append(rendered_content)

        reasoning = message.get("reasoning_content")
        if reasoning:
            parts.append(
                "ASSISTANT REASONING\n"
                + _truncate_for_summary(
                    _render_value(reasoning),
                    max_chars=reasoning_max_chars,
                    label="reasoning",
                )
            )

        tool_calls = message.get("tool_calls") or []
        for tool_call in tool_calls:
            function = dict(tool_call.get("function") or {})
            parts.append(
                "TOOL CALL "
                f"id={tool_call.get('id', '')} "
                f"name={function.get('name', '')} "
                f"arguments={_render_value(function.get('arguments', '{}'))}",
            )
        rendered.append("\n".join(parts))
    return rendered


def _truncate_for_summary(
    text: str,
    *,
    max_chars: int,
    label: str,
) -> str:
    """Bound verbose evidence while retaining the outcome and final diagnosis."""
    limit = max(1, int(max_chars))
    if len(text) <= limit:
        return text
    marker = f"\n\n[... {len(text) - limit} {label} characters omitted ...]\n\n"
    payload = max(2, limit - len(marker))
    head = payload // 2
    tail = payload - head
    return text[:head] + marker + text[-tail:]


def _validate_summary(summary: str) -> None:
    """Reject malformed checkpoints instead of replacing valid history."""
    positions = [summary.find(heading) for heading in _REQUIRED_SUMMARY_HEADINGS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("compaction summary is missing the required ordered headings")


def _estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate only Accumulated messages using payload-safe serialization."""
    safe = _sanitize_for_compaction(messages)
    try:
        text = json.dumps(safe, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(safe)
    return max(1, len(text) // 4)


def _render_value(value: Any) -> str:
    sanitized = _sanitize_for_compaction(value)
    if isinstance(sanitized, str):
        return sanitized
    try:
        return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(sanitized)


def _sanitize_for_compaction(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"[binary content omitted: {len(value)} bytes]"
    if isinstance(value, list):
        return [_sanitize_for_compaction(item) for item in value]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"data", "base64"} and isinstance(item, str) and len(item) > 256:
                sanitized[key] = f"[base64 omitted: {len(item)} chars]"
                continue
            sanitized[str(key)] = _sanitize_for_compaction(item)
        return sanitized
    if isinstance(value, str):
        return re.sub(
            r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+",
            "[image payload omitted]",
            value,
        )
    return value


def _message_metadata_value(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if value in (None, ""):
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get(key)
    return str(value or "").strip()


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "COMPACTION_SUMMARY_MESSAGE_KIND",
    "COMPACTED_ACCUMULATED_MESSAGES_PREFIX",
    "CompactionConfig",
    "ContextCompactor",
    "HARNESS_COMPACTION_SOURCE",
    "HARNESS_POST_EXECUTION_HOOK_SOURCE",
]
