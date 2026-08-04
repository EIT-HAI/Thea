from __future__ import annotations

from typing import Any

import pytest
from harness.context import Context, ModelResponse, ToolCall
from harness.evaluation.contract import (
    EVALUATE_RUN_TOOL_NAME,
    EvaluationRequest,
    EvaluatorTool,
    EvaluatorVerdict,
)
from harness.evaluation.model import (
    EvaluatorResponseError,
    ModelEvaluator,
)
from harness.runtime.core import Harness
from harness.tools.registry import BuiltinTool, ToolRegistry
from harness.world.observation import (
    BaseClearance,
    Observation,
    VisualEvidence,
)

POST_CONDITION = "The target is visibly held above its original support."
TOOL_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class RecordingEvaluationModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.calls: list[tuple[Context, list[dict[str, Any]] | None]] = []

    def call(
        self,
        context: Context,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        self.calls.append((context, tools))
        return self.response


def physical_tool() -> BuiltinTool:
    return BuiltinTool(
        name="pick",
        description="Pick the selected object.",
        input_schema=TOOL_SCHEMA,
        fn=lambda: {"success": True, "run_id": "run-7"},
        post_condition=POST_CONDITION,
    )


def test_model_evaluator_builds_an_independent_multimodal_context() -> None:
    model = RecordingEvaluationModel(
        ModelResponse(
            text=(
                "```json\n"
                '{"status":"success","evidence":["object is held"],'
                '"failure_reason":""}\n'
                "```"
            )
        )
    )
    evaluator = ModelEvaluator(model)
    observation = Observation(
        visuals=(
            VisualEvidence.from_bytes(
                "front",
                b"\xff\xd8\xff\xe0sample",
                media_type="image/jpeg",
                caption="Post-execution front view",
            ),
        ),
        base_clearance=BaseClearance(
            forward_m=0.8,
            backward_m=1.0,
            left_m=0.7,
            right_m=0.9,
        ),
        captured_at="2026-07-28T12:00:00Z",
        provenance="robot_rgbd",
    )

    verdict = evaluator.evaluate(
        EvaluationRequest(
            post_condition=POST_CONDITION,
            post_execution_observation=observation,
        )
    )

    assert verdict == EvaluatorVerdict(
        status="success",
        evidence=("object is held",),
    )
    assert len(model.calls) == 1
    context, tools = model.calls[0]
    assert tools == []
    assert POST_CONDITION in context.provider_system_content
    assert "active instruction" in context.provider_system_content
    messages = context.messages_for_model()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    blocks = messages[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["caption"] == "Post-execution front view"


def test_model_evaluator_rejects_untyped_post_execution_evidence() -> None:
    model = RecordingEvaluationModel(
        ModelResponse(
            text=('{"status":"success","evidence":["visible"],"failure_reason":""}')
        )
    )

    with pytest.raises(TypeError, match="must be an Observation"):
        ModelEvaluator(model).evaluate(
            EvaluationRequest(
                post_condition=POST_CONDITION,
                post_execution_observation={"image_path": "/private/front.jpg"},
            )
        )

    assert model.calls == []


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(text="not JSON"),
        ModelResponse(text='{"status":"unknown","evidence":[],"failure_reason":""}'),
        ModelResponse(text='{"status":"failure","evidence":[],"failure_reason":""}'),
        ModelResponse(text='{"status":true,"evidence":[],"failure_reason":""}'),
        ModelResponse(text='{"status":"success","evidence":[],"failure_reason":[]}'),
        ModelResponse(
            text=(
                '{"status":"success","evidence":[],'
                '"failure_reason":"should not be present"}'
            )
        ),
        ModelResponse(text='{"status":"success","evidence":{},"failure_reason":""}'),
        ModelResponse(
            text=(
                '{"status":"success","evidence":[],"failure_reason":"",'
                '"recommended_recovery":{"command":"move_forward"}}'
            )
        ),
        ModelResponse(
            text='{"status":"success","evidence":[],"failure_reason":""}',
            tool_calls=[ToolCall("call-1", "pick", {})],
        ),
    ],
)
def test_model_evaluator_rejects_malformed_verdicts(
    response: ModelResponse,
) -> None:
    model = RecordingEvaluationModel(response)

    with pytest.raises(EvaluatorResponseError):
        ModelEvaluator(model).evaluate(
            EvaluationRequest(
                post_condition=POST_CONDITION,
                post_execution_observation=Observation(
                    summary="The gripper is visible."
                ),
            )
        )


def test_hidden_evaluate_run_accepts_the_model_evaluator() -> None:
    class PostExecutionObservationProvider:
        def __init__(self) -> None:
            self.run_ids: list[str] = []

        def capture(self, run_id: str) -> Observation:
            self.run_ids.append(run_id)
            return Observation(summary="The object is visibly held.")

    model = RecordingEvaluationModel(
        ModelResponse(
            text=(
                '{"status":"success","evidence":["object is held"],"failure_reason":""}'
            )
        )
    )
    observation_provider = PostExecutionObservationProvider()
    registry = ToolRegistry()
    registry.register(physical_tool())
    registry.register(
        EvaluatorTool(
            registry=registry,
            evaluator=ModelEvaluator(model),
            observation_provider=observation_provider,
        )
    )

    result = registry.call_tool(
        EVALUATE_RUN_TOOL_NAME,
        {
            "run_id": "run-7",
            "target_tool_name": "pick",
        },
    )

    assert result == {
        "success": True,
        "run_id": "run-7",
        "evaluation": {
            "status": "success",
            "evidence": ["object is held"],
            "failure_reason": "",
        },
    }
    assert observation_provider.run_ids == ["run-7"]
    assert len(model.calls) == 1


def test_harness_automatically_runs_model_evaluation_after_physical_tool() -> None:
    class AgentModel:
        def __init__(self) -> None:
            self.turn = 0

        def call(
            self,
            context: Context,
            tools: list[dict[str, Any]] | None = None,
        ) -> ModelResponse:
            self.turn += 1
            assert [definition["name"] for definition in tools or []] == ["pick"]
            if self.turn == 1:
                return ModelResponse(tool_calls=[ToolCall("pick-1", "pick", {})])
            tool_results = [
                message["content"]
                for message in context.accumulated_messages
                if message.get("role") == "tool"
            ]
            assert tool_results[-1]["evaluator_verdict"] == {
                "status": "success",
                "evidence": ["object is held"],
                "failure_reason": "",
            }
            return ModelResponse(text="Done.")

    class PostExecutionObservationProvider:
        def capture(self, run_id: str) -> Observation:
            assert run_id == "run-7"
            return Observation(summary="The object is visibly held.")

    evaluation_model = RecordingEvaluationModel(
        ModelResponse(
            text=(
                '{"status":"success","evidence":["object is held"],"failure_reason":""}'
            )
        )
    )
    registry = ToolRegistry()
    registry.register(physical_tool())
    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
            "evaluation": {
                "required_tools": ["pick"],
                "segment_tools": [],
                "post_conditions": {},
            },
        },
        model=AgentModel(),
        registry=registry,
        evaluator=ModelEvaluator(evaluation_model),
        post_execution_observation_provider=(PostExecutionObservationProvider()),
    )
    try:
        events = list(instance.run_stream("Pick the object."))
    finally:
        instance.close()

    assert any(
        event.get("type") == "tool_call"
        and event.get("name") == EVALUATE_RUN_TOOL_NAME
        and event.get("post_execution_hook") is True
        and event.get("triggered_by") == "pick"
        for event in events
    )
    assert events[-1]["type"] == "done"
    assert len(evaluation_model.calls) == 1
