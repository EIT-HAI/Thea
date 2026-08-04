from __future__ import annotations

from typing import Any

import pytest
from harness.configuration.runtime import ConfigurationError, validate_runtime_config
from harness.context import Context, ToolCall
from harness.evaluation.contract import (
    EVALUATE_RUN_TOOL_NAME,
    EvaluationRequest,
    EvaluatorPostExecutionHook,
    EvaluatorTool,
    EvaluatorVerdict,
)
from harness.runtime.core import Harness
from harness.runtime.hooks import ToolHooks
from harness.tools.publisher import ToolResultPublisher
from harness.tools.registry import BuiltinTool, ToolRegistry

POST_CONDITION = "The target is visibly held above its original support."
TOOL_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class RecordingObservationProvider:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    def capture(self, run_id: str) -> dict[str, Any]:
        self.run_ids.append(run_id)
        return {"views": ["front"], "holding": True}


class RecordingEvaluator:
    def __init__(self, verdict: EvaluatorVerdict) -> None:
        self.verdict = verdict
        self.requests: list[EvaluationRequest] = []

    def evaluate(self, request: EvaluationRequest) -> EvaluatorVerdict:
        self.requests.append(request)
        return self.verdict


def test_evaluator_verdict_enforces_the_three_state_contract() -> None:
    with pytest.raises(ValueError, match="process, success, or failure"):
        EvaluatorVerdict(status="unknown")
    with pytest.raises(ValueError, match="failure_reason"):
        EvaluatorVerdict(status="failure")
    with pytest.raises(TypeError, match="sequence of strings"):
        EvaluatorVerdict(status="success", evidence="visible")


def physical_tool(*, post_condition: str | None = POST_CONDITION) -> BuiltinTool:
    return BuiltinTool(
        name="pick",
        description="Pick the selected object.",
        input_schema=TOOL_SCHEMA,
        fn=lambda: {"success": True, "run_id": "run-7"},
        post_condition=post_condition,
    )


def registry_with_evaluator(
    verdict: EvaluatorVerdict,
    *,
    post_condition: str | None = POST_CONDITION,
) -> tuple[ToolRegistry, RecordingObservationProvider, RecordingEvaluator]:
    registry = ToolRegistry()
    registry.register(physical_tool(post_condition=post_condition))
    observation_provider = RecordingObservationProvider()
    evaluator = RecordingEvaluator(verdict)
    registry.register(
        EvaluatorTool(
            registry=registry,
            evaluator=evaluator,
            observation_provider=observation_provider,
        )
    )
    return registry, observation_provider, evaluator


@pytest.mark.parametrize(
    ("verdict", "expected_status"),
    [
        (EvaluatorVerdict(status="success", evidence=("object lifted",)), "success"),
        (
            EvaluatorVerdict(
                status="failure",
                evidence=("object remains on table",),
                failure_reason="The grasp missed the object.",
            ),
            "failure",
        ),
        (EvaluatorVerdict(status="process", evidence=("arm still moving",)), "process"),
    ],
)
def test_hidden_evaluator_adapter_preserves_three_statuses_and_request_inputs(
    verdict: EvaluatorVerdict,
    expected_status: str,
) -> None:
    registry, observation_provider, evaluator = registry_with_evaluator(verdict)

    result = registry.call_tool(
        EVALUATE_RUN_TOOL_NAME,
        {
            "run_id": "run-7",
            "target_tool_name": "pick",
        },
    )

    assert result["success"] is True
    assert result["evaluation"]["status"] == expected_status
    assert observation_provider.run_ids == ["run-7"]
    assert evaluator.requests == [
        EvaluationRequest(
            post_condition=POST_CONDITION,
            post_execution_observation={
                "views": ["front"],
                "holding": True,
            },
        )
    ]


def test_post_execution_hook_passes_only_evaluator_evidence_handles() -> None:
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
    )
    call = ToolCall("call-1", "pick", {"target": "cup_2"})

    evaluator_call = hook.build_call(
        call,
        {
            "success": True,
            "run_id": "run-1",
            "observation": "policy reached the target",
            "backend_status": {"terminated": True},
        },
    )

    assert evaluator_call is not None
    assert evaluator_call.arguments == {
        "run_id": "run-1",
        "target_tool_name": "pick",
    }


def test_evaluated_execution_requires_run_id_even_when_tool_reports_failure() -> None:
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
    )
    call = ToolCall("call-1", "pick", {"target": "cup_2"})

    result = hook.normalize_execution_result(
        call,
        {
            "success": False,
            "reason": "The policy stopped before reaching the target.",
        },
    )

    assert result["success"] is False
    assert result["kind"] == "missing_evaluation_run_id"
    assert "cannot be judged" in result["reason"]
    assert "policy stopped" in result["reason"]


def test_tool_reported_failure_with_run_id_still_builds_evaluator_call() -> None:
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
    )
    call = ToolCall("call-1", "pick", {"target": "cup_2"})
    tool_result = hook.normalize_execution_result(
        call,
        {
            "success": False,
            "reason": "The policy reported a miss.",
            "run_id": "run-failed",
        },
    )

    evaluator_call = hook.build_call(call, tool_result)

    assert evaluator_call is not None
    assert evaluator_call.arguments == {
        "run_id": "run-failed",
        "target_tool_name": "pick",
    }


def test_evaluator_tool_is_hidden_and_not_model_callable() -> None:
    registry, _, _ = registry_with_evaluator(EvaluatorVerdict(status="success"))
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
    )

    hook.validate(registry)
    visible = hook.model_visible_definitions(registry.list_tool_definitions())
    rejection = hook.tool_call_rejection(
        ToolCall(id="model-call", name=EVALUATE_RUN_TOOL_NAME, arguments={})
    )

    assert [definition["name"] for definition in visible] == ["pick"]
    assert set(visible[0]) == {"name", "description", "inputSchema"}
    assert rejection is not None
    assert rejection["kind"] == "internal_tool_not_model_callable"


def test_required_tool_must_declare_a_post_condition() -> None:
    registry, _, _ = registry_with_evaluator(
        EvaluatorVerdict(status="success"),
        post_condition=None,
    )
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
    )

    with pytest.raises(ConfigurationError, match="no post-condition: pick"):
        hook.validate(registry)


def test_mcp_post_condition_configures_public_registry_boundary() -> None:
    class MCPClient:
        def list_tools(self) -> list[dict[str, Any]]:
            return [
                {
                    "name": "pick",
                    "description": "Pick the selected object.",
                    "inputSchema": TOOL_SCHEMA,
                    "annotations": {"private": "not model-visible"},
                }
            ]

        def call_tool(
            self,
            name: str,
            arguments: dict[str, Any],
            *,
            timeout: float | None = None,
        ) -> dict[str, Any]:
            del name, arguments, timeout
            return {"success": True, "run_id": "run-7"}

        def close(self) -> None:
            return None

    class Model:
        def call(self, context, tools=None):
            raise AssertionError("The model is not used by this assembly test.")

    observation_provider = RecordingObservationProvider()
    evaluator = RecordingEvaluator(EvaluatorVerdict(status="success"))
    instance = Harness(
        {
            "servers": [],
            "evaluation": {
                "required_tools": ["pick"],
                "post_conditions": {"pick": POST_CONDITION},
            },
        },
        model=Model(),
        mcp_client=MCPClient(),
        evaluator=evaluator,
        post_execution_observation_provider=observation_provider,
    )
    try:
        instance.hooks.validate_hook_configuration()
        definitions = instance.hooks.model_visible_definitions(
            instance.registry.list_tool_definitions()
        )

        assert instance.registry.post_condition("pick") == POST_CONDITION
        assert instance.registry.post_conditions() == {"pick": POST_CONDITION}
        assert instance.registry.has(EVALUATE_RUN_TOOL_NAME)
        assert definitions == [
            {
                "name": "pick",
                "description": "Pick the selected object.",
                "inputSchema": TOOL_SCHEMA,
            }
        ]
    finally:
        instance.close()


def test_evaluation_config_rejects_invalid_post_condition_mapping() -> None:
    with pytest.raises(ConfigurationError, match="post_conditions"):
        validate_runtime_config(
            {
                "evaluation": {
                    "post_conditions": {"pick": ""},
                }
            }
        )


def test_no_evaluation_configuration_keeps_evaluator_inactive() -> None:
    class MCPClient:
        def list_tools(self) -> list[dict[str, Any]]:
            return []

        def call_tool(
            self,
            name: str,
            arguments: dict[str, Any],
            *,
            timeout: float | None = None,
        ) -> dict[str, Any]:
            raise AssertionError((name, arguments, timeout))

        def close(self) -> None:
            return None

    class Model:
        def call(self, context, tools=None):
            raise AssertionError("The model is not used by this assembly test.")

    registry = ToolRegistry()
    registry.register(physical_tool(post_condition=None))
    instance = Harness(
        {"servers": []},
        model=Model(),
        mcp_client=MCPClient(),
        registry=registry,
        evaluator=RecordingEvaluator(EvaluatorVerdict(status="success")),
        post_execution_observation_provider=RecordingObservationProvider(),
    )
    try:
        instance.hooks.validate_hook_configuration()
        assert not instance.registry.has(EVALUATE_RUN_TOOL_NAME)
        assert [item["name"] for item in instance.registry.list_tool_definitions()] == [
            "pick"
        ]
    finally:
        instance.close()


def test_process_is_protocol_failure_for_non_segment_tool() -> None:
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
    )
    evaluator_call = ToolCall(
        id="evaluate",
        name=EVALUATE_RUN_TOOL_NAME,
        arguments={"run_id": "run-7", "target_tool_name": "pick"},
    )

    result = hook.normalize_execution_result(
        evaluator_call,
        {
            "success": True,
            "evaluation": {
                "status": "process",
                "evidence": ["arm still moving"],
                "failure_reason": "",
            },
        },
    )

    assert result["success"] is False
    assert result["kind"] == "evaluator_protocol_violation"
    assert result["evaluation"]["status"] == "failure"


def test_process_remains_valid_for_declared_segment_tool() -> None:
    hook = EvaluatorPostExecutionHook(
        required_tools=frozenset({"pick"}),
        segment_tools=frozenset({"pick"}),
    )
    evaluator_call = ToolCall(
        id="evaluate",
        name=EVALUATE_RUN_TOOL_NAME,
        arguments={"run_id": "run-7", "target_tool_name": "pick"},
    )

    result = hook.normalize_execution_result(
        evaluator_call,
        {
            "success": True,
            "evaluation": {
                "status": "process",
                "evidence": ["arm still moving"],
                "failure_reason": "",
            },
        },
    )

    assert result["success"] is True
    assert result["evaluation"]["status"] == "process"


def test_only_evaluator_success_updates_execution_derived_scene_graph() -> None:
    class TaskNotes:
        def append_tool_result(self, **_kwargs) -> None:
            return None

    class Memory:
        task_notes = TaskNotes()

    class SceneGraph:
        def __init__(self) -> None:
            self.verdicts: list[str] = []

        def apply_confirmed_execution(
            self,
            *,
            tool_name,
            arguments,
            result,
            evaluator_verdict,
        ):
            del tool_name, arguments, result
            self.verdicts.append(evaluator_verdict["status"])
            return {"updated": True}

    graph = SceneGraph()
    publisher = ToolResultPublisher(
        hooks=ToolHooks(ToolRegistry(), {}),
        memory=Memory(),
        scene_graph=graph,
    )
    call = ToolCall(id="call-1", name="pick", arguments={"ref": "bottle_1"})

    for index, verdict in enumerate(
        (
            None,
            {"status": "process", "evidence": [], "failure_reason": ""},
            {
                "status": "failure",
                "evidence": [],
                "failure_reason": "not held",
            },
            {"status": "success", "evidence": ["held"], "failure_reason": ""},
        ),
        start=1,
    ):
        publisher.publish(
            call,
            {"success": True, "run_id": f"run-{index}"},
            evaluator_verdict=verdict,
            context=Context(),
            turn=index,
        )

    assert graph.verdicts == ["success"]
