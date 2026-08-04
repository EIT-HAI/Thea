"""Publish a completed Tool Result to its context and state boundaries."""

from __future__ import annotations

from typing import Any

from harness.context import Context, ToolCall
from harness.protocols import MemoryProtocol, SceneGraphProtocol
from harness.runtime.execution_types import PostExecutionOutcome
from harness.runtime.hooks import ToolHooks
from harness.tools.evidence import (
    tool_result_for_model,
    transient_tool_result_evidence_message,
)


class ToolResultPublisher:
    """Publish one Tool Result, Task Note, and confirmed Scene Graph update."""

    def __init__(
        self,
        *,
        hooks: ToolHooks,
        memory: MemoryProtocol,
        scene_graph: SceneGraphProtocol,
    ) -> None:
        self._hooks = hooks
        self._memory = memory
        self._scene_graph = scene_graph

    def publish(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        evaluator_verdict: dict[str, Any] | None,
        context: Context,
        turn: int,
        proposed_call: ToolCall | None = None,
    ) -> PostExecutionOutcome:
        """Publish after the Evaluator has reached an exit code."""
        events: list[dict[str, Any]] = []
        _record_model_facing_result(
            context,
            call,
            result,
            evaluator_verdict=evaluator_verdict,
            proposed_call=proposed_call,
        )
        note_result = (
            _result_with_evaluator_verdict(result, evaluator_verdict)
            if evaluator_verdict is not None
            else result
        )
        events.extend(
            _append_task_note(
                self._memory,
                call,
                note_result,
                turn=turn,
                evaluator_verdict=evaluator_verdict,
            )
        )
        if evaluator_verdict is not None and self._hooks.confirms_execution(
            evaluator_verdict
        ):
            events.extend(
                self._apply_confirmed_scene_graph_update(
                    call,
                    result,
                    evaluator_verdict=evaluator_verdict,
                    turn=turn,
                )
            )
        return PostExecutionOutcome(events, evaluator_verdict)

    def _apply_confirmed_scene_graph_update(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        evaluator_verdict: dict[str, Any],
        turn: int,
    ) -> list[dict[str, Any]]:
        try:
            update = self._scene_graph.apply_confirmed_execution(
                tool_name=call.name,
                arguments=call.arguments,
                result=result,
                evaluator_verdict=evaluator_verdict,
            )
        except Exception as exc:
            return [
                _post_execution_hook_error(
                    stage="scene_graph_update",
                    call=call,
                    turn=turn,
                    error=exc,
                )
            ]
        if update is None:
            return []
        return [
            {
                "type": "scene_graph_update",
                "turn": turn,
                "source": "evaluator_confirmed_execution",
                "update": update,
            }
        ]


def _append_task_note(
    memory: MemoryProtocol,
    call: ToolCall,
    result: dict[str, Any],
    *,
    turn: int,
    evaluator_verdict: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Record task-local continuity without owning the physical Tool Result."""
    try:
        memory.task_notes.append_tool_result(
            tool=call.name,
            result=result,
            arguments=call.arguments,
            evaluator_verdict=evaluator_verdict,
        )
    except Exception as exc:
        return [
            _post_execution_hook_error(
                stage="task_notes",
                call=call,
                turn=turn,
                error=exc,
            )
        ]
    return []


def _post_execution_hook_error(
    *,
    stage: str,
    call: ToolCall,
    turn: int,
    error: Exception,
) -> dict[str, Any]:
    """Expose a failed post-execution hook without hiding Tool evidence."""
    return {
        "type": "post_execution_hook_error",
        "turn": turn,
        "id": call.id,
        "name": call.name,
        "stage": stage,
        "error": f"{type(error).__name__}: {error}",
        "tool_result_recorded": True,
    }


def _record_model_facing_result(
    context: Context,
    call: ToolCall,
    result: dict[str, Any],
    *,
    evaluator_verdict: dict[str, Any] | None = None,
    proposed_call: ToolCall | None = None,
) -> None:
    model_result = tool_result_for_model(result)
    if proposed_call is not None and proposed_call.arguments != call.arguments:
        model_result["executed_arguments"] = dict(call.arguments)
    if evaluator_verdict is not None:
        model_result["evaluator_verdict"] = dict(evaluator_verdict)
    context.add_tool_result(call.id, model_result)
    evidence = transient_tool_result_evidence_message(call, result)
    if evidence is not None:
        context.add_transient_tool_result_message(evidence)


def _result_with_evaluator_verdict(
    result: dict[str, Any],
    evaluator_verdict: dict[str, Any],
) -> dict[str, Any]:
    """Attach Evaluator evidence to the event stored in Task Notes."""
    merged = dict(result)
    merged["evaluator_verdict"] = dict(evaluator_verdict)
    return merged


__all__ = ["ToolResultPublisher"]
