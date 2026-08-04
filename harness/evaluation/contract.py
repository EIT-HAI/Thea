"""Evaluator post-execution hook for policy-backed tools.

The evaluator is a harness-internal tool. It is never model-visible and its
invocation is derived from the completed physical tool call rather than from a
second model decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from harness.configuration.runtime import ConfigurationError
from harness.context import ToolCall
from harness.tools.registry import ToolRegistry

EVALUATE_RUN_TOOL_NAME = "evaluate_run"
EVALUATOR_VERDICT_STATUSES = frozenset({"process", "success", "failure"})
EvaluatorStatus = Literal["process", "success", "failure"]


@dataclass(frozen=True)
class EvaluationRequest:
    """The two evidence inputs exposed to an independent evaluator."""

    post_condition: str
    post_execution_observation: Any


@dataclass(frozen=True)
class EvaluatorVerdict:
    """Paper-aligned three-state outcome returned by an evaluator provider."""

    status: EvaluatorStatus
    evidence: tuple[str, ...] = ()
    failure_reason: str = ""

    def __post_init__(self) -> None:
        status = str(self.status).strip().lower()
        if status not in EVALUATOR_VERDICT_STATUSES:
            raise ValueError(
                "EvaluatorVerdict.status must be process, success, or failure"
            )
        if isinstance(self.evidence, (str, bytes)):
            raise TypeError("EvaluatorVerdict.evidence must be a sequence of strings")
        evidence = tuple(
            str(item).strip() for item in self.evidence if str(item).strip()
        )
        failure_reason = str(self.failure_reason).strip()
        if status == "failure" and not failure_reason:
            raise ValueError(
                "EvaluatorVerdict.failure_reason must be non-empty for failure"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "evidence",
            evidence,
        )
        object.__setattr__(
            self,
            "failure_reason",
            failure_reason,
        )


@runtime_checkable
class EvaluatorProtocol(Protocol):
    """Independent component that judges one post-execution Observation."""

    def evaluate(self, request: EvaluationRequest) -> EvaluatorVerdict: ...


@runtime_checkable
class PostExecutionObservationProvider(Protocol):
    """Capture objective evidence associated with one completed tool run."""

    def capture(self, run_id: str) -> Any: ...


class EvaluatorTool:
    """Hidden adapter from the shared Tool boundary to public providers."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        evaluator: EvaluatorProtocol,
        observation_provider: PostExecutionObservationProvider,
    ) -> None:
        self._registry = registry
        self._evaluator = evaluator
        self._observation_provider = observation_provider

    @property
    def name(self) -> str:
        return EVALUATE_RUN_TOOL_NAME

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "name": EVALUATE_RUN_TOOL_NAME,
            "description": (
                "Harness-internal post-execution evaluator. "
                "The model cannot call this tool."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "minLength": 1},
                    "target_tool_name": {"type": "string", "minLength": 1},
                },
                "required": ["run_id", "target_tool_name"],
                "additionalProperties": False,
            },
        }

    def call(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = str(args.get("run_id") or "").strip()
        target_tool_name = str(args.get("target_tool_name") or "").strip()
        post_condition = self._registry.post_condition(target_tool_name)
        if post_condition is None:
            return {
                "success": False,
                "kind": "missing_evaluation_post_condition",
                "reason": (
                    f"Tool {target_tool_name!r} has no evaluator post-condition."
                ),
            }

        observation = self._observation_provider.capture(run_id)
        verdict = self._evaluator.evaluate(
            EvaluationRequest(
                post_condition=post_condition,
                post_execution_observation=observation,
            )
        )
        if not isinstance(verdict, EvaluatorVerdict):
            return {
                "success": False,
                "kind": "evaluator_protocol_violation",
                "reason": (
                    "EvaluatorProtocol.evaluate() must return EvaluatorVerdict."
                ),
            }
        return {
            "success": True,
            "run_id": run_id,
            "evaluation": {
                "status": verdict.status,
                "evidence": list(verdict.evidence),
                "failure_reason": verdict.failure_reason,
            },
        }


@dataclass(frozen=True)
class EvaluatorPostExecutionHook:
    """Build and interpret evaluator calls at the execution boundary."""

    required_tools: frozenset[str]
    segment_tools: frozenset[str] = frozenset()
    max_segments: int = 8

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> EvaluatorPostExecutionHook:
        evaluation = config.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        return cls(
            required_tools=frozenset(
                str(name).strip()
                for name in evaluation.get("required_tools") or []
                if str(name).strip()
            ),
            segment_tools=frozenset(
                str(name).strip()
                for name in evaluation.get("segment_tools") or []
                if str(name).strip()
            ),
            max_segments=max(1, int(evaluation.get("max_segments", 8))),
        )

    def validate(self, registry: ToolRegistry) -> None:
        """Fail at task start when a post-execution hook cannot run."""
        unconfigured_segment_tools = sorted(self.segment_tools - self.required_tools)
        if unconfigured_segment_tools:
            raise ConfigurationError(
                "Evaluation segment_tools must also appear in required_tools: "
                + ", ".join(unconfigured_segment_tools)
            )
        if not self.required_tools:
            return
        if EVALUATE_RUN_TOOL_NAME in self.required_tools:
            raise ConfigurationError(
                "evaluate_run is harness-internal and cannot evaluate itself."
            )
        missing_tools = sorted(self.required_tools - registry.names())
        if missing_tools:
            raise ConfigurationError(
                "Evaluation-required tools are not registered: "
                + ", ".join(missing_tools)
            )
        missing_post_conditions = sorted(
            name
            for name in self.required_tools
            if registry.post_condition(name) is None
        )
        if missing_post_conditions:
            raise ConfigurationError(
                "Evaluation-required tools have no post-condition: "
                + ", ".join(missing_post_conditions)
            )
        if not registry.has(EVALUATE_RUN_TOOL_NAME):
            raise ConfigurationError(
                "Evaluation-required tools are configured, but evaluate_run "
                "is not registered."
            )

    def model_visible_definitions(
        self,
        definitions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Hide the harness-internal evaluator from model tool definitions."""
        return [
            definition
            for definition in definitions
            if definition.get("name") != EVALUATE_RUN_TOOL_NAME
        ]

    @staticmethod
    def tool_call_rejection(call: ToolCall) -> dict[str, Any] | None:
        """Reject a model-guessed call to the harness-internal evaluator."""
        if call.name != EVALUATE_RUN_TOOL_NAME:
            return None
        return {
            "success": False,
            "kind": "internal_tool_not_model_callable",
            "reason": (
                "evaluate_run is invoked only by the harness after configured "
                "physical tools; it cannot be selected by the model."
            ),
        }

    def normalize_execution_result(
        self,
        call: ToolCall,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate evaluator verdicts and reject unverifiable physical success."""
        if call.name == EVALUATE_RUN_TOOL_NAME:
            normalized = _normalize_evaluator_result(result)
            evaluation = normalized.get("evaluation")
            status = (
                str(evaluation.get("status") or "").strip().lower()
                if isinstance(evaluation, dict)
                else ""
            )
            target_tool_name = str(call.arguments.get("target_tool_name") or "").strip()
            if status == "process" and target_tool_name not in self.segment_tools:
                return _evaluator_protocol_failure(
                    "evaluation.status may be process only for a tool listed "
                    "in config.evaluation.segment_tools"
                )
            return normalized
        if call.name not in self.required_tools:
            return result
        if str(result.get("run_id") or "").strip():
            return result
        original_reason = str(result.get("reason") or "").strip()
        reason = (
            f"Evaluation-required tool {call.name!r} returned without a run_id "
            "after execution; its physical outcome cannot be judged."
        )
        if original_reason:
            reason += f" Tool-reported reason: {original_reason}"
        return {
            "success": False,
            "reason": reason,
            "kind": "missing_evaluation_run_id",
        }

    def build_call(
        self,
        call: ToolCall,
        result: dict[str, Any],
    ) -> ToolCall | None:
        """Return the structural evaluator call for one completed execution.

        The run id and tool name are handles for the post-execution Observation
        and per-tool post-condition. The active instruction, tool arguments,
        main-model reasoning, and self-report are not evaluator evidence.
        """
        if call.name not in self.required_tools:
            return None
        run_id = str(result.get("run_id") or "").strip()
        if not run_id:
            return None
        return ToolCall(
            id=f"{call.id}_evaluate",
            name=EVALUATE_RUN_TOOL_NAME,
            arguments={
                "run_id": run_id,
                "target_tool_name": call.name,
            },
        )

    @staticmethod
    def verdict(result: dict[str, Any]) -> dict[str, Any] | None:
        """Extract the paper's Evaluator Verdict from the internal Tool Result."""
        value = result.get("evaluation")
        if not isinstance(value, dict):
            return None
        status = str(value.get("status") or "").strip().lower()
        if status not in EVALUATOR_VERDICT_STATUSES:
            return None
        evidence = value.get("evidence")
        evidence = evidence if isinstance(evidence, list) else []
        return {
            "status": status,
            "evidence": [str(item).strip() for item in evidence if str(item).strip()],
            "failure_reason": str(value.get("failure_reason") or "").strip(),
        }

    @classmethod
    def confirms_execution(cls, evaluator_verdict: dict[str, Any]) -> bool:
        return str(evaluator_verdict.get("status") or "").strip().lower() == "success"

    def continues_action_segments(self, call: ToolCall) -> bool:
        """Whether one call executes a single resumable action segment."""
        return call.name in self.segment_tools


def _normalize_evaluator_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate the evaluator's three-state contract at the harness boundary."""
    if not bool(result.get("success")):
        return _evaluator_unavailable(result)

    evaluation = result.get("evaluation")
    if not isinstance(evaluation, dict):
        return _evaluator_protocol_failure(
            "successful evaluator call omitted the evaluation object"
        )

    status = str(evaluation.get("status") or "").strip().lower()
    if status not in EVALUATOR_VERDICT_STATUSES:
        return _evaluator_protocol_failure(
            "evaluation.status must be process, success, or failure"
        )

    normalized = dict(result)
    normalized_evaluation = dict(evaluation)
    normalized_evaluation["status"] = status
    evidence = normalized_evaluation.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence] if evidence.strip() else []
    if not isinstance(evidence, list):
        evidence = []
    normalized_evaluation["evidence"] = [
        str(item).strip() for item in evidence if str(item).strip()
    ]
    failure_reason = str(
        normalized_evaluation.get("failure_reason")
        or (result.get("reason") if status == "failure" else "")
        or ""
    ).strip()
    if status == "failure" and not failure_reason:
        return _evaluator_protocol_failure(
            "evaluation.failure_reason must be non-empty when status is failure"
        )
    normalized_evaluation["failure_reason"] = failure_reason
    normalized["evaluation"] = normalized_evaluation
    return normalized


def _evaluator_unavailable(result: dict[str, Any]) -> dict[str, Any]:
    """Represent evaluator transport/runtime failure with the stable verdict shape."""
    reason = str(
        result.get("reason") or "Evaluator failed without returning a verdict."
    ).strip()
    normalized = dict(result)
    normalized.update(
        {
            "success": False,
            "reason": reason,
            "kind": str(result.get("kind") or "evaluator_unavailable"),
            "evaluation": {
                "status": "failure",
                "evidence": [],
                "failure_reason": reason,
            },
        }
    )
    return normalized


def _evaluator_protocol_failure(detail: str) -> dict[str, Any]:
    reason = f"Evaluator protocol violation: {detail}."
    return {
        "success": False,
        "reason": reason,
        "kind": "evaluator_protocol_violation",
        "evaluation": {
            "status": "failure",
            "evidence": [],
            "failure_reason": reason,
        },
    }


__all__ = [
    "EVALUATE_RUN_TOOL_NAME",
    "EVALUATOR_VERDICT_STATUSES",
    "EvaluationRequest",
    "EvaluatorProtocol",
    "EvaluatorPostExecutionHook",
    "EvaluatorStatus",
    "EvaluatorTool",
    "EvaluatorVerdict",
    "PostExecutionObservationProvider",
]
