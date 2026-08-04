"""Model-backed reference implementation of the Evaluator boundary."""

from __future__ import annotations

import json
from typing import Any

from harness.context import Context
from harness.evaluation.contract import (
    EvaluationRequest,
    EvaluatorVerdict,
)
from harness.protocols import ModelProtocol
from harness.world.observation import (
    Observation,
    render_observation_messages,
)

_EVALUATOR_INSTRUCTIONS = """\
You are an independent evaluator for one completed physical tool execution.
Judge only whether the post-execution Observation satisfies the supplied
POST_CONDITION. The POST_CONDITION is the authoritative outcome criterion.
Use only observed evidence. Do not rely on the acting model's reasoning,
self-report, active instruction, or unstated task assumptions.

Return one JSON object with exactly these fields:
- "status": "process", "success", or "failure";
- "evidence": an array of concise observed facts supporting the verdict;
- "failure_reason": the cause of failure, or an empty string otherwise.

Judge the outcome only. Do not recommend a recovery action, tool, direction,
distance, or retry.
"""


class EvaluatorResponseError(ValueError):
    """Raised when an evaluator model does not return a valid verdict."""


class ModelEvaluator:
    """Judge a post-condition with any provider-neutral multimodal model.

    The deployment supplies a dedicated ``ModelProtocol`` implementation and
    returns a typed :class:`Observation` from its post-execution provider.
    The acting agent's Context is never passed to this model.
    """

    def __init__(
        self,
        model: ModelProtocol,
        *,
        max_visuals: int = 3,
    ) -> None:
        if isinstance(max_visuals, bool) or not isinstance(max_visuals, int):
            raise TypeError("max_visuals must be an integer")
        if max_visuals < 0:
            raise ValueError("max_visuals must be non-negative")
        self._model = model
        self._max_visuals = max_visuals

    def evaluate(self, request: EvaluationRequest) -> EvaluatorVerdict:
        """Return one three-state verdict from post-execution evidence."""
        context = _evaluation_context(
            request,
            max_visuals=self._max_visuals,
        )
        response = self._model.call(context, tools=[])
        if response.tool_calls:
            raise EvaluatorResponseError(
                "Evaluator model must return a verdict, not a Tool Call."
            )
        payload = _parse_json_object(response.text)
        return _verdict_from_payload(payload)


def _evaluation_context(
    request: EvaluationRequest,
    *,
    max_visuals: int,
) -> Context:
    observation = request.post_execution_observation
    if not isinstance(observation, Observation):
        raise TypeError(
            "ModelEvaluator.post_execution_observation must be an Observation."
        )

    context = Context()
    context.set_context_layers(
        resident=(
            f"{_EVALUATOR_INSTRUCTIONS}\nPOST_CONDITION:\n{request.post_condition}"
        ),
    )
    messages = render_observation_messages(
        observation,
        max_visuals=max_visuals,
    )
    if messages:
        context.add_user_message(messages[0]["content"])
    else:
        context.add_user_message(
            [
                {
                    "type": "text",
                    "text": _observation_without_sensor_payload(observation),
                }
            ]
        )
    return context


def _observation_without_sensor_payload(observation: Observation) -> str:
    details = ["Post-execution Observation contains no visual or clearance data."]
    if observation.summary:
        details.append(f"Summary: {observation.summary}")
    if observation.captured_at not in (None, ""):
        details.append(f"Captured at: {observation.captured_at}")
    if observation.provenance:
        details.append(f"Provenance: {observation.provenance}")
    return "\n".join(details)


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise EvaluatorResponseError("Evaluator model returned an empty response.")

    candidates = [raw]
    if raw.startswith("```") and raw.endswith("```"):
        body = raw[3:-3].strip()
        if body.lower().startswith("json"):
            body = body[4:].lstrip()
        candidates.insert(0, body)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = _first_embedded_json_object(candidate, decoder)
        if isinstance(value, dict):
            return value
    raise EvaluatorResponseError("Evaluator model did not return a valid JSON object.")


def _first_embedded_json_object(
    text: str,
    decoder: json.JSONDecoder,
) -> dict[str, Any] | None:
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _verdict_from_payload(payload: dict[str, Any]) -> EvaluatorVerdict:
    expected_fields = {"status", "evidence", "failure_reason"}
    actual_fields = set(payload)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unexpected = sorted(actual_fields - expected_fields)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise EvaluatorResponseError(
            "Evaluator response must contain exactly status, evidence, and "
            f"failure_reason ({'; '.join(details)})."
        )

    raw_status = payload.get("status")
    if not isinstance(raw_status, str):
        raise EvaluatorResponseError(
            "Evaluator response field 'status' must be a string."
        )
    status = raw_status.strip().lower()
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        raise EvaluatorResponseError(
            "Evaluator response field 'evidence' must be an array of strings."
        )
    if any(not isinstance(item, str) for item in evidence):
        raise EvaluatorResponseError(
            "Evaluator response field 'evidence' must contain only strings."
        )
    failure_reason = payload.get("failure_reason")
    if not isinstance(failure_reason, str):
        raise EvaluatorResponseError(
            "Evaluator response field 'failure_reason' must be a string."
        )
    if status in {"process", "success"} and failure_reason.strip():
        raise EvaluatorResponseError(
            "Evaluator response field 'failure_reason' must be empty unless "
            "status is failure."
        )
    try:
        return EvaluatorVerdict(
            status=status,
            evidence=tuple(evidence),
            failure_reason=failure_reason,
        )
    except (TypeError, ValueError) as exc:
        raise EvaluatorResponseError(str(exc)) from exc


__all__ = [
    "EvaluatorResponseError",
    "ModelEvaluator",
]
