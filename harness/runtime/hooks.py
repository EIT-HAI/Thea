"""Deterministic pre- and post-execution hooks owned by the harness."""

from __future__ import annotations

from typing import Any

from harness.context import ObservationSink, ToolCall
from harness.evaluation.contract import EvaluatorPostExecutionHook
from harness.tools.registry import ToolRegistry
from harness.world.safety import (
    BaseClearanceProvider,
    BaseMotionSafetyFilter,
    PreExecutionHook,
    PreExecutionHookResult,
)


class ToolHooks:
    """Run non-bypassable pre- and post-execution interception."""

    def __init__(
        self,
        registry: ToolRegistry,
        config: dict[str, Any],
        *,
        base_clearance_provider: BaseClearanceProvider | None = None,
        pre_execution_hook: PreExecutionHook | None = None,
    ) -> None:
        if pre_execution_hook is not None and base_clearance_provider is not None:
            raise ValueError(
                "base_clearance_provider cannot be combined with an explicit "
                "pre_execution_hook"
            )
        self.registry = registry
        self.evaluator = EvaluatorPostExecutionHook.from_config(config)
        self.pre_execution_hook = (
            BaseMotionSafetyFilter.from_config(
                config,
                clearance_provider=base_clearance_provider,
            )
            if pre_execution_hook is None
            else pre_execution_hook
        )

    def bind_registry(self, registry: ToolRegistry) -> None:
        """Bind hook execution to the active Tool Registry."""
        self.registry = registry

    def validate_hook_configuration(self) -> None:
        self.evaluator.validate(self.registry)

    def model_visible_definitions(
        self,
        definitions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.evaluator.model_visible_definitions(definitions)

    def normalize_execution_result(
        self,
        call: ToolCall,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.evaluator.normalize_execution_result(call, result)

    def tool_call_rejection(
        self,
        call: ToolCall,
    ) -> dict[str, Any] | None:
        return self.evaluator.tool_call_rejection(call)

    def build_evaluator_call(
        self,
        call: ToolCall,
        result: dict[str, Any],
    ) -> ToolCall | None:
        return self.evaluator.build_call(call, result)

    def evaluator_verdict(
        self,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self.evaluator.verdict(result)

    def confirms_execution(
        self,
        evaluator_verdict: dict[str, Any],
    ) -> bool:
        return self.evaluator.confirms_execution(evaluator_verdict)

    def evaluator_continues_action_segments(self, call: ToolCall) -> bool:
        return self.evaluator.continues_action_segments(call)

    @property
    def evaluator_segment_budget(self) -> int:
        return self.evaluator.max_segments

    def run_pre_execution(
        self,
        call: ToolCall,
        *,
        turn: int,
    ) -> PreExecutionHookResult:
        return self.pre_execution_hook.apply(
            call,
            registry=self.registry,
            turn=turn,
        )

    def refresh_decision_context(
        self,
        sink: ObservationSink,
        *,
        turn: int,
    ) -> dict[str, Any] | None:
        """Refresh model-facing safety evidence when using the default filter."""
        if not isinstance(self.pre_execution_hook, BaseMotionSafetyFilter):
            return None
        return self.pre_execution_hook.refresh_decision_context(
            sink,
            registry=self.registry,
            turn=turn,
        )


__all__ = ["PreExecutionHookResult", "ToolHooks"]
