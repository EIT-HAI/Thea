"""Task-scoped loop state and cooperative control polling."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeAlias, TypeVar

from harness.context import TASK_NOTES_MESSAGE_KIND, Context
from harness.protocols import MemoryProtocol

T = TypeVar("T")
logger = logging.getLogger(__name__)
TaskStatus: TypeAlias = Literal["completed", "aborted"]

_COMPLETED_MODEL_STOP_REASONS = frozenset(
    {
        "",
        "end_turn",
        "stop",
        "stop_sequence",
    }
)
_TOOL_MODEL_STOP_REASONS = frozenset(
    {
        "tool_calls",
        "tool_use",
        "function_call",
    }
)
_FAILED_MODEL_STOP_REASONS = frozenset(
    {
        "content_filter",
        "error",
        "refusal",
        "safety",
    }
)


@dataclass(frozen=True)
class PollResult(Generic[T]):
    """Value returned by a cooperative control poll plus any error event."""

    value: T
    error_event: dict[str, Any] | None = None


@dataclass
class TaskState:
    """Mutable state whose lifetime is exactly one user instruction."""

    instruction: str
    turn_budget: int
    failure_budget: int
    consecutive_failures: int = 0

    @classmethod
    def create(
        cls,
        instruction: str,
        *,
        max_turns: int,
        failure_budget: int,
    ) -> TaskState:
        return cls(
            instruction=str(instruction),
            turn_budget=max(1, int(max_turns)),
            failure_budget=max(1, int(failure_budget)),
        )

    def record_turn_outcome(
        self,
        tool_result: dict[str, Any],
        evaluator_verdict: dict[str, Any] | None,
    ) -> None:
        """Count one final turn outcome and reset the budget after success."""
        tool_failed = not bool(tool_result.get("success"))
        evaluator_status = str((evaluator_verdict or {}).get("status") or "").strip()
        evaluator_failed = evaluator_status == "failure"
        self._record_turn_failure(tool_failed or evaluator_failed)

    def _record_turn_failure(self, failed: bool) -> None:
        self.consecutive_failures = self.consecutive_failures + 1 if failed else 0

    @property
    def failure_budget_exhausted(self) -> bool:
        return self.consecutive_failures >= self.failure_budget

    def revise_instruction(self, instruction: str) -> None:
        self.instruction = str(instruction)


@dataclass(frozen=True)
class ModelStopOutcome:
    """An abnormal provider stop that cannot complete the active task."""

    task_status: TaskStatus
    termination_reason: str
    stop_reason: str
    final_text: str


@dataclass(frozen=True)
class TaskOutcome:
    """The single runtime outcome returned by one task."""

    task_status: TaskStatus
    termination_reason: str
    final_text: str
    turns: int
    stop_reason: str = ""


def abnormal_model_stop(
    stop_reason: str,
    *,
    has_tool_calls: bool | None = None,
) -> ModelStopOutcome | None:
    """Classify provider stops that must not be treated as task completion.

    Provider-native successful stops differ in spelling. All other non-empty
    reasons fail closed so a truncated, filtered, paused, or otherwise
    unfinished generation cannot silently complete a physical task.
    """
    reason = str(stop_reason or "").strip().lower()
    if has_tool_calls:
        if reason == "" or reason in _TOOL_MODEL_STOP_REASONS:
            return None
        return ModelStopOutcome(
            task_status="aborted",
            termination_reason="model_stop_tool_call_mismatch",
            stop_reason=reason,
            final_text=(
                "The model returned a Tool Call with an incompatible "
                f"stop reason ({reason})."
            ),
        )

    if reason in _COMPLETED_MODEL_STOP_REASONS:
        return None
    if reason in _TOOL_MODEL_STOP_REASONS:
        return ModelStopOutcome(
            task_status="aborted",
            termination_reason="model_stop_tool_call_mismatch",
            stop_reason=reason,
            final_text=(
                "The model reported a Tool Call stop without returning a Tool Call."
            ),
        )
    if reason in _FAILED_MODEL_STOP_REASONS:
        return ModelStopOutcome(
            task_status="aborted",
            termination_reason=f"model_stop_{reason}",
            stop_reason=reason,
            final_text=f"The model response was blocked or failed ({reason}).",
        )
    return ModelStopOutcome(
        task_status="aborted",
        termination_reason=f"model_stop_{reason or 'unknown'}",
        stop_reason=reason,
        final_text=(
            f"The model response ended before the decision completed ({reason})."
        ),
    )


def task_done_event(
    outcome: TaskOutcome,
) -> dict[str, Any]:
    """Build the one final event emitted for a fully consumed task stream."""
    event: dict[str, Any] = {
        "type": "done",
        "turns": outcome.turns,
        "final_text": outcome.final_text,
        "task_status": outcome.task_status,
        "termination_reason": outcome.termination_reason,
    }
    if outcome.stop_reason:
        event["stop_reason"] = outcome.stop_reason
    return event


def consolidate_task_notes_at_task_end(
    *,
    memory: MemoryProtocol,
    allowed_tools: Iterable[str],
    turn: int,
    consolidate: bool = True,
    skip_reason: str = "",
) -> dict[str, Any]:
    """Consolidate completed Task Notes into the two durable stores."""
    if consolidate:
        try:
            event = memory.consolidate(allowed_tools=allowed_tools)
        except Exception as exc:
            event = {
                "type": "memory_consolidation",
                "success": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "memory_written": 0,
                "tool_experience_written": 0,
            }
    else:
        event = {
            "type": "memory_consolidation",
            "success": False,
            "skipped": True,
            "skip_reason": skip_reason or "task_not_consolidated",
            "memory_written": 0,
            "tool_experience_written": 0,
        }
    return {
        **event,
        "turn": turn,
    }


def clear_task_scope(
    *,
    memory: MemoryProtocol,
    context: Context | None,
    resident_context: Callable[[], str],
) -> None:
    """Best-effort cleanup after return, generator close, or any exception."""
    try:
        memory.task_notes.expire()
    except Exception:
        logger.exception("Failed to expire Task Notes during task cleanup")

    if context is None:
        return
    context.remove_accumulated_messages_by_kind(TASK_NOTES_MESSAGE_KIND)
    context.clear_observation_messages()
    context.clear_transient_tool_result_messages()
    try:
        context.set_context_layers(
            resident=resident_context(),
            refreshed=context.refreshed_context,
        )
    except Exception:
        logger.exception("Failed to clear task-scoped Resident context")


def revised_instruction_message(text: str) -> str:
    """Render a user revision inside the same task."""
    return (
        "The user revised the active instruction within this task. "
        f"Replan from the latest Observation:\n{text}"
    )


def poll_cancellation(
    callback: Callable[[], bool] | None,
    *,
    turn: int,
) -> PollResult[bool]:
    """Poll one cancellation source without letting callback errors escape."""

    if callback is None:
        return PollResult(False)
    try:
        return PollResult(bool(callback()))
    except Exception as exc:
        return PollResult(
            False,
            {
                "type": "cancellation_poll_error",
                "turn": turn,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def poll_replan(
    callback: Callable[[], str] | None,
    *,
    turn: int,
) -> PollResult[str]:
    """Poll one instruction-revision source and normalize its next value."""

    if callback is None:
        return PollResult("")
    try:
        return PollResult(str(callback() or "").strip())
    except Exception as exc:
        return PollResult(
            "",
            {
                "type": "replan_poll_error",
                "turn": turn,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def user_interaction_abort_text(result: dict[str, Any]) -> str | None:
    """Return task-abort text for a completed user-interaction Tool Result."""
    kind = str(result.get("kind") or "")
    if kind not in {"query_user_timeout", "query_user_cancelled"}:
        return None
    reason = str(result.get("reason") or "user interaction ended")
    return f"The task stopped because user interaction ended: {reason}"


__all__ = [
    "ModelStopOutcome",
    "PollResult",
    "TaskOutcome",
    "TaskState",
    "TaskStatus",
    "abnormal_model_stop",
    "clear_task_scope",
    "consolidate_task_notes_at_task_end",
    "poll_cancellation",
    "poll_replan",
    "revised_instruction_message",
    "task_done_event",
    "user_interaction_abort_text",
]
