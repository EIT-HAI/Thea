"""Continue explicitly declared action segments until an Evaluator exit code."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from harness.context import Context, ToolCall
from harness.runtime.execution_types import (
    EvaluatedExecutionOutcome,
    EvaluatorHookOutcome,
    PostExecutionOutcome,
    ToolExecutionRecord,
)
from harness.runtime.hooks import ToolHooks


class SegmentExecutor(Protocol):
    """Execute one physical action segment."""

    def __call__(
        self,
        call: ToolCall,
        *,
        turn: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ToolExecutionRecord: ...


class EvaluatorInvoker(Protocol):
    """Invoke the Evaluator for one completed action segment."""

    def __call__(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        turn: int,
    ) -> EvaluatorHookOutcome: ...


class ToolResultFinalizer(Protocol):
    """Publish a Tool Result after its Evaluator exit code."""

    def __call__(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        evaluator: EvaluatorHookOutcome,
        context: Context,
        turn: int,
        proposed_call: ToolCall | None = None,
    ) -> PostExecutionOutcome: ...


class ActionSegmentRunner:
    """Own continuation, cancellation, and budget for one segmented action."""

    def __init__(
        self,
        *,
        hooks: ToolHooks,
        execute_segment: SegmentExecutor,
        invoke_evaluator: EvaluatorInvoker,
        finalize_result: ToolResultFinalizer,
    ) -> None:
        self._hooks = hooks
        self._execute_segment = execute_segment
        self._invoke_evaluator = invoke_evaluator
        self._finalize_result = finalize_result

    def run(
        self,
        call: ToolCall,
        *,
        context: Context,
        turn: int,
        segment_budget: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> EvaluatedExecutionOutcome:
        """Repeat a declared action segment while the status is ``process``."""
        if not self._hooks.evaluator_continues_action_segments(call):
            raise ValueError(
                f"Tool {call.name!r} is not configured as an action-segment tool."
            )
        budget = max(1, int(segment_budget))
        execution = self._execute_segment(
            call,
            turn=turn,
            should_cancel=should_cancel,
        )
        effective_call = execution.effective_call or call
        events: list[dict[str, Any]] = []
        segment_count = 0
        cancelled = False
        cancellation_reason = ""

        while True:
            segment_count += 1
            events.extend(_segment_events(execution.events, segment_count))
            if execution.cancelled:
                evaluator = EvaluatorHookOutcome((), None)
                cancelled = True
                cancellation_reason = "cancelled_before_tool_execution"
                break

            evaluator = self._invoke_evaluator(
                effective_call,
                execution.result,
                turn=turn,
            )
            events.extend(_segment_events(evaluator.events, segment_count))
            evaluator_status = str(
                (evaluator.evaluator_verdict or {}).get("status") or ""
            )
            if evaluator_status != "process":
                break

            cancellation_event, cancelled = poll_segment_cancellation(
                should_cancel,
                call=effective_call,
                turn=turn,
                segment_index=segment_count,
            )
            if cancellation_event is not None:
                events.append(cancellation_event)
            if cancelled:
                cancellation_reason = "cancelled_between_action_segments"
                evaluator = _segment_cancellation(
                    call=effective_call,
                    segment_count=segment_count,
                    turn=turn,
                )
                events.extend(evaluator.events)
                break
            if segment_count >= budget:
                evaluator = _segment_budget_failure(
                    call=effective_call,
                    segment_count=segment_count,
                    segment_budget=budget,
                    turn=turn,
                )
                events.extend(evaluator.events)
                break

            events.append(
                {
                    "type": "evaluator_process_continuation",
                    "turn": turn,
                    "id": effective_call.id,
                    "name": effective_call.name,
                    "segment_index": segment_count,
                    "next_segment_index": segment_count + 1,
                    "segment_budget": budget,
                }
            )
            execution = self._execute_segment(
                effective_call,
                turn=turn,
                should_cancel=should_cancel,
            )
            effective_call = execution.effective_call or effective_call

        finalized = self._finalize_result(
            effective_call,
            execution.result,
            evaluator=evaluator,
            context=context,
            turn=turn,
            proposed_call=call,
        )
        events.extend(finalized.events)
        return EvaluatedExecutionOutcome(
            call=effective_call,
            result=execution.result,
            events=tuple(events),
            evaluator_verdict=finalized.evaluator_verdict,
            segment_count=segment_count,
            cancelled=cancelled,
            cancellation_reason=cancellation_reason,
        )


def poll_before_tool_execution_cancellation(
    callback: Callable[[], bool] | None,
    *,
    call: ToolCall,
    turn: int,
) -> tuple[dict[str, Any] | None, bool]:
    """Poll once after pre-execution hooks and before tool implementation."""
    if callback is None:
        return None, False
    try:
        return None, bool(callback())
    except Exception as exc:
        return (
            {
                "type": "cancellation_poll_error",
                "turn": turn,
                "id": call.id,
                "name": call.name,
                "stage": "before_tool_execution",
                "error": f"{type(exc).__name__}: {exc}",
            },
            False,
        )


def poll_segment_cancellation(
    callback: Callable[[], bool] | None,
    *,
    call: ToolCall,
    turn: int,
    segment_index: int,
) -> tuple[dict[str, Any] | None, bool]:
    """Poll once before continuing to another action segment."""
    if callback is None:
        return None, False
    try:
        return None, bool(callback())
    except Exception as exc:
        return (
            {
                "type": "cancellation_poll_error",
                "turn": turn,
                "id": call.id,
                "name": call.name,
                "segment_index": segment_index,
                "error": f"{type(exc).__name__}: {exc}",
            },
            False,
        )


def _segment_events(
    events: tuple[dict[str, Any], ...],
    segment_index: int,
) -> list[dict[str, Any]]:
    return [{**event, "segment_index": segment_index} for event in events]


def _segment_budget_failure(
    *,
    call: ToolCall,
    segment_count: int,
    segment_budget: int,
    turn: int,
) -> EvaluatorHookOutcome:
    reason = (
        f"Action remained in process after {segment_count} segments; "
        f"segment_budget={segment_budget} was exhausted."
    )
    return EvaluatorHookOutcome(
        events=(
            {
                "type": "evaluator_segment_budget_exhausted",
                "turn": turn,
                "id": call.id,
                "name": call.name,
                "segment_count": segment_count,
                "segment_budget": segment_budget,
                "status": "failure",
                "reason": reason,
            },
        ),
        evaluator_verdict={
            "status": "failure",
            "evidence": [reason],
            "failure_reason": reason,
        },
    )


def _segment_cancellation(
    *,
    call: ToolCall,
    segment_count: int,
    turn: int,
) -> EvaluatorHookOutcome:
    reason = "Task cancellation stopped evaluation process continuation."
    return EvaluatorHookOutcome(
        events=(
            {
                "type": "evaluator_process_cancelled",
                "turn": turn,
                "id": call.id,
                "name": call.name,
                "segment_count": segment_count,
                "status": "failure",
                "reason": reason,
            },
        ),
        evaluator_verdict={
            "status": "failure",
            "evidence": [reason],
            "failure_reason": reason,
        },
    )


__all__ = [
    "ActionSegmentRunner",
    "EvaluatorInvoker",
    "SegmentExecutor",
    "ToolResultFinalizer",
    "poll_before_tool_execution_cancellation",
    "poll_segment_cancellation",
]
