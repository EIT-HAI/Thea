"""Uniform tool execution and post-execution processing."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from harness.context import Context, ToolCall
from harness.evaluation.runner import EvaluatorRunner
from harness.protocols import MemoryProtocol, SceneGraphProtocol
from harness.runtime.action_segments import (
    ActionSegmentRunner,
    poll_before_tool_execution_cancellation,
)
from harness.runtime.execution_types import (
    EvaluatedExecutionOutcome,
    EvaluatorHookOutcome,
    PostExecutionOutcome,
)
from harness.runtime.hooks import ToolHooks
from harness.tools.publisher import ToolResultPublisher
from harness.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolExecution:
    """One blocking tool execution and its stable run events."""

    result: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    effective_call: ToolCall | None = None
    cancelled: bool = False


class ToolResultEffect(Protocol):
    """Apply one state-owned effect after a Tool Result is available."""

    def apply_tool_result(
        self,
        call: ToolCall,
        execution: ToolExecution,
    ) -> ToolExecution: ...


class ToolExecutionPipeline:
    """Apply validation, hooks, implementation, and result recording in order."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        hooks: ToolHooks,
        memory: MemoryProtocol,
        scene_graph: SceneGraphProtocol,
        tool_result_effects: Iterable[ToolResultEffect] = (),
    ) -> None:
        self.registry = registry
        self.hooks = hooks
        self.memory = memory
        self.scene_graph = scene_graph
        self.tool_result_effects = tuple(tool_result_effects)
        self._evaluator_runner = EvaluatorRunner(
            hooks=hooks,
            execute_tool=self.execute,
        )
        self._tool_result_publisher = ToolResultPublisher(
            hooks=hooks,
            memory=memory,
            scene_graph=scene_graph,
        )
        self._action_segment_runner = ActionSegmentRunner(
            hooks=hooks,
            execute_segment=self.execute_selected,
            invoke_evaluator=self.run_evaluator,
            finalize_result=self.finalize_post_execution,
        )

    def execute_selected(
        self,
        call: ToolCall,
        *,
        turn: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ToolExecution:
        """Execute the one model-selected call for this turn."""
        internal_rejection = self.hooks.tool_call_rejection(call)
        if internal_rejection is not None:
            return self._apply_tool_result_effects(
                call,
                _intercepted_execution(
                    call,
                    internal_rejection,
                    turn,
                ),
            )
        validation_failure = self.registry.validate_call(call.name, call.arguments)
        if validation_failure is not None:
            return self._apply_tool_result_effects(
                call,
                _intercepted_execution(call, validation_failure, turn),
            )

        pre_execution = self.hooks.run_pre_execution(
            call,
            turn=turn,
        )
        if pre_execution.blocked_result is not None:
            return self._apply_tool_result_effects(
                call,
                _intercepted_execution(
                    call,
                    pre_execution.blocked_result,
                    turn,
                    hook_events=pre_execution.events,
                    handled_by_pre_execution_hook=True,
                ),
            )

        cancellation_event, cancelled = poll_before_tool_execution_cancellation(
            should_cancel,
            call=pre_execution.call,
            turn=turn,
        )
        cancellation_events = (
            (cancellation_event,) if cancellation_event is not None else ()
        )
        if cancelled:
            return self._apply_tool_result_effects(
                pre_execution.call,
                _intercepted_execution(
                    pre_execution.call,
                    {
                        "success": False,
                        "reason": ("Task cancelled before the selected tool executed."),
                        "kind": "task_cancelled",
                    },
                    turn,
                    hook_events=(
                        *pre_execution.events,
                        *cancellation_events,
                    ),
                    handled_by_pre_execution_hook=True,
                    cancelled=True,
                ),
            )

        execution = self.execute(pre_execution.call, turn=turn)
        return self._apply_tool_result_effects(
            pre_execution.call,
            ToolExecution(
                execution.result,
                (
                    *pre_execution.events,
                    *cancellation_events,
                    *execution.events,
                ),
                effective_call=pre_execution.call,
            ),
        )

    def _apply_tool_result_effects(
        self,
        call: ToolCall,
        execution: ToolExecution,
    ) -> ToolExecution:
        """Pass a result through state boundaries without tool-name branches."""
        outcome = execution
        for effect in self.tool_result_effects:
            outcome = effect.apply_tool_result(call, outcome)
        return outcome

    def execute(
        self,
        call: ToolCall,
        *,
        turn: int,
        post_execution_hook: bool = False,
        triggered_by: str = "",
    ) -> ToolExecution:
        """Execute one registry call and emit raw execution events.

        These events support observability. The result enters model context
        only after :meth:`apply_post_execution` has attached any evaluator
        verdict.
        """
        hook_fields = (
            {"post_execution_hook": True, "triggered_by": triggered_by}
            if post_execution_hook
            else {}
        )
        call_event = {
            "type": "tool_call",
            "turn": turn,
            "id": call.id,
            "name": call.name,
            "arguments": dict(call.arguments),
            **hook_fields,
        }
        started = time.monotonic()
        result = self.registry.call_tool(call.name, call.arguments)
        result = self.hooks.normalize_execution_result(call, result)
        result_event = {
            "type": "tool_result",
            "turn": turn,
            "id": call.id,
            "name": call.name,
            "success": bool(result.get("success")),
            "result": result,
            "duration_ms": int((time.monotonic() - started) * 1000),
            **hook_fields,
        }
        return ToolExecution(
            result=result,
            events=(call_event, result_event),
            effective_call=call,
        )

    def apply_post_execution(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        turn: int,
        context: Context,
        proposed_call: ToolCall | None = None,
    ) -> PostExecutionOutcome:
        """Finalize the model-facing Tool Result after post-execution hooks."""
        evaluator = self.run_evaluator(
            call,
            result,
            turn=turn,
        )
        outcome = self.finalize_post_execution(
            call,
            result,
            evaluator=evaluator,
            context=context,
            turn=turn,
            proposed_call=proposed_call,
        )
        return PostExecutionOutcome(
            [*evaluator.events, *outcome.events],
            outcome.evaluator_verdict,
        )

    def run_evaluator(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        turn: int,
    ) -> EvaluatorHookOutcome:
        """Run the internal evaluator once for one completed action segment."""
        return self._evaluator_runner.run(
            call,
            result,
            turn=turn,
        )

    def finalize_post_execution(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        evaluator: EvaluatorHookOutcome,
        context: Context,
        turn: int,
        proposed_call: ToolCall | None = None,
    ) -> PostExecutionOutcome:
        """Publish one final Tool Result after evaluation reaches an exit code."""
        return self._tool_result_publisher.publish(
            call,
            result,
            evaluator_verdict=evaluator.evaluator_verdict,
            context=context,
            turn=turn,
            proposed_call=proposed_call,
        )

    def execute_selected_tool_call(
        self,
        call: ToolCall,
        *,
        context: Context,
        turn: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> EvaluatedExecutionOutcome:
        """Execute one model-selected call under its declared action boundary.

        Most tools represent a complete execution and therefore run exactly
        once before control returns to the model. A tool listed explicitly in
        ``evaluation.segment_tools`` represents one resumable action segment;
        only those tools may repeat internally while the evaluator returns
        ``process``.
        """
        if self.hooks.evaluator_continues_action_segments(call):
            return self.execute_until_evaluator_exit(
                call,
                context=context,
                turn=turn,
                segment_budget=self.hooks.evaluator_segment_budget,
                should_cancel=should_cancel,
            )

        execution = self.execute_selected(
            call,
            turn=turn,
            should_cancel=should_cancel,
        )
        effective_call = execution.effective_call or call
        if execution.cancelled:
            finalized = self.finalize_post_execution(
                effective_call,
                execution.result,
                evaluator=EvaluatorHookOutcome((), None),
                context=context,
                turn=turn,
                proposed_call=call,
            )
            return EvaluatedExecutionOutcome(
                call=effective_call,
                result=execution.result,
                events=(*execution.events, *finalized.events),
                evaluator_verdict=None,
                segment_count=0,
                cancelled=True,
                cancellation_reason="cancelled_before_tool_execution",
            )
        evaluator = self.run_evaluator(
            effective_call,
            execution.result,
            turn=turn,
        )
        finalized = self.finalize_post_execution(
            effective_call,
            execution.result,
            evaluator=evaluator,
            context=context,
            turn=turn,
            proposed_call=call,
        )
        return EvaluatedExecutionOutcome(
            call=effective_call,
            result=execution.result,
            events=(
                *execution.events,
                *evaluator.events,
                *finalized.events,
            ),
            evaluator_verdict=finalized.evaluator_verdict,
            segment_count=1,
        )

    def execute_until_evaluator_exit(
        self,
        call: ToolCall,
        *,
        context: Context,
        turn: int,
        segment_budget: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> EvaluatedExecutionOutcome:
        """Repeat an explicitly declared action segment while status is process."""
        return self._action_segment_runner.run(
            call,
            context=context,
            turn=turn,
            segment_budget=segment_budget,
            should_cancel=should_cancel,
        )

    @staticmethod
    def reject_extra_calls(
        calls: list[ToolCall],
        *,
        turn: int,
        context: Context,
    ) -> list[dict[str, Any]]:
        """Record unexecuted calls when a model emits more than one."""
        return ToolExecutionPipeline.record_unexecuted_calls(
            calls,
            turn=turn,
            context=context,
            reason=(
                "The harness executes one model-selected tool per turn. "
                "Replan this call after observing the first result."
            ),
            kind="one_tool_per_turn",
        )

    @staticmethod
    def record_unexecuted_calls(
        calls: Iterable[ToolCall],
        *,
        turn: int,
        context: Context,
        reason: str,
        kind: str,
    ) -> list[dict[str, Any]]:
        """Pair every pending Tool Call with an explicit unexecuted result."""
        events: list[dict[str, Any]] = []
        resolved_ids = {
            str(message.get("tool_call_id") or "")
            for message in context.accumulated_messages
            if message.get("role") == "tool"
        }
        for call in calls:
            if call.id in resolved_ids:
                continue
            result = {
                "success": False,
                "reason": reason,
                "kind": kind,
                "executed": False,
            }
            context.add_tool_result(call.id, result)
            resolved_ids.add(call.id)
            events.append(
                {
                    "type": "tool_result",
                    "turn": turn,
                    "id": call.id,
                    "name": call.name,
                    "success": False,
                    "result": result,
                    "duration_ms": 0,
                    "executed": False,
                }
            )
        return events


def _intercepted_execution(
    call: ToolCall,
    result: dict[str, Any],
    turn: int,
    *,
    hook_events: tuple[dict[str, Any], ...] = (),
    handled_by_pre_execution_hook: bool = False,
    cancelled: bool = False,
) -> ToolExecution:
    result_event = {
        "type": "tool_result",
        "turn": turn,
        "id": call.id,
        "name": call.name,
        "success": bool(result.get("success")),
        "result": result,
        "duration_ms": 0,
        "executed": False,
    }
    if handled_by_pre_execution_hook:
        result_event["handled_by_pre_execution_hook"] = True
        if not bool(result.get("success")):
            result_event["blocked_by_pre_execution_hook"] = True
    return ToolExecution(
        result=result,
        events=(
            *hook_events,
            {
                "type": "tool_call",
                "turn": turn,
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments),
            },
            result_event,
        ),
        effective_call=call,
        cancelled=cancelled,
    )


__all__ = [
    "EvaluatedExecutionOutcome",
    "EvaluatorHookOutcome",
    "PostExecutionOutcome",
    "ToolExecution",
    "ToolExecutionPipeline",
]
