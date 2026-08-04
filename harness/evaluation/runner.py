"""Invoke the harness-internal Evaluator after a physical Tool Result."""

from __future__ import annotations

from typing import Any, Protocol

from harness.context import ToolCall
from harness.runtime.execution_types import EvaluatorHookOutcome, ToolExecutionRecord
from harness.runtime.hooks import ToolHooks


class EvaluatorToolExecutor(Protocol):
    """Execute the internal Evaluator through the shared Tool boundary."""

    def __call__(
        self,
        call: ToolCall,
        *,
        turn: int,
        post_execution_hook: bool = False,
        triggered_by: str = "",
    ) -> ToolExecutionRecord: ...


class EvaluatorRunner:
    """Build, invoke, and interpret one harness-owned Evaluator call."""

    def __init__(
        self,
        *,
        hooks: ToolHooks,
        execute_tool: EvaluatorToolExecutor,
    ) -> None:
        self._hooks = hooks
        self._execute_tool = execute_tool

    def run(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        turn: int,
    ) -> EvaluatorHookOutcome:
        """Return the Evaluator Verdict for one completed action segment."""
        evaluator_call = self._hooks.build_evaluator_call(call, result)
        if evaluator_call is None:
            return EvaluatorHookOutcome((), None)

        execution = self._execute_tool(
            evaluator_call,
            turn=turn,
            post_execution_hook=True,
            triggered_by=call.name,
        )
        return EvaluatorHookOutcome(
            execution.events,
            self._hooks.evaluator_verdict(execution.result),
        )


__all__ = ["EvaluatorRunner", "EvaluatorToolExecutor"]
