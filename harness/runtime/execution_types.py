"""Shared value types for the tool-execution boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from harness.context import ToolCall


class ToolExecutionRecord(Protocol):
    """Structural view of one blocking Tool execution."""

    result: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    effective_call: ToolCall | None
    cancelled: bool


class PostExecutionOutcome(NamedTuple):
    """Evaluator outcome and events produced before the next model call."""

    events: list[dict[str, Any]]
    evaluator_verdict: dict[str, Any] | None


@dataclass(frozen=True)
class EvaluatorHookOutcome:
    """One post-execution evaluator invocation and its verdict."""

    events: tuple[dict[str, Any], ...]
    evaluator_verdict: dict[str, Any] | None


@dataclass(frozen=True)
class EvaluatedExecutionOutcome:
    """Final outcome of one model-selected call and its evaluator boundary."""

    call: ToolCall
    result: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    evaluator_verdict: dict[str, Any] | None
    segment_count: int
    cancelled: bool = False
    cancellation_reason: str = ""


__all__ = [
    "EvaluatedExecutionOutcome",
    "EvaluatorHookOutcome",
    "PostExecutionOutcome",
    "ToolExecutionRecord",
]
