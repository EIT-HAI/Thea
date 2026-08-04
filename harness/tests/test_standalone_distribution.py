from __future__ import annotations

import ast
from pathlib import Path

import harness
import pytest
from harness import BuiltinTool, Harness, ModelResponse, ToolCall, ToolRegistry
from harness.context import TASK_NOTES_MESSAGE_KIND
from harness.models import ModelCallError
from harness.runtime.standalone import NullMemory


class _TrackingMemory(NullMemory):
    def __init__(self) -> None:
        super().__init__()
        self.consolidation_calls = 0

    def consolidate(self, *, allowed_tools):
        self.consolidation_calls += 1
        return super().consolidate(allowed_tools=allowed_tools)


def test_public_package_imports() -> None:
    assert harness.__version__ == "0.1.0"
    assert Harness.__module__ == "harness.runtime.core"
    expected = {
        "BaseClearance",
        "BaseClearanceProvider",
        "BuiltinTool",
        "EmbodimentProfile",
        "EvaluationRequest",
        "EvaluatorProtocol",
        "EvaluatorResponseError",
        "EvaluatorVerdict",
        "FileMemory",
        "Harness",
        "ModelEvaluator",
        "Observation",
        "PreExecutionHookResult",
        "RuntimeResource",
        "SceneGraphBrief",
        "SceneGraphQueryProtocol",
        "TaskNotes",
        "ToolRegistry",
        "VisualEvidence",
        "register_scene_graph_query_tools",
        "render_observation_messages",
        "render_scene_graph_brief",
    }
    assert expected.issubset(harness.__all__)
    assert "SimulationRuntime" not in harness.__all__
    assert "ObservationToolAccess" not in harness.__all__
    assert "observation_tool_access" not in harness.__all__
    assert all(hasattr(harness, name) for name in harness.__all__)


def test_harness_rejects_incomplete_configured_embodiment_profile(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "robot.md"
    profile.write_text(
        "# Embodiment Profile\n\n"
        "## Operational Envelope\n\n"
        "### Base Footprint\n"
        "0.4 m radius.\n",
        encoding="utf-8",
    )

    class FinalModel:
        def call(self, context, tools=None):
            del context, tools
            return ModelResponse(text="Done.")

    with pytest.raises(ValueError, match="Invalid Embodiment Profile"):
        Harness(
            {
                "servers": [],
                "embodiment_profile_file": str(profile),
                "context": {"compaction": {"enabled": False}},
            },
            model=FinalModel(),
        )


def test_two_turn_standalone_agentic_loop() -> None:
    class TwoTurnModel:
        def __init__(self) -> None:
            self.turn = 0

        def call(self, context, tools=None):
            self.turn += 1
            notes = [
                message
                for message in context.accumulated_messages
                if message.get("kind") == TASK_NOTES_MESSAGE_KIND
            ]
            assert len(notes) == self.turn
            if self.turn == 1:
                assert [tool["name"] for tool in tools or []] == ["echo"]
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            name="echo",
                            arguments={"text": "hello"},
                        )
                    ]
                )
            return ModelResponse(text="Done.")

    registry = ToolRegistry()
    registry.register(
        BuiltinTool(
            name="echo",
            description="Echo text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            fn=lambda text: {"success": True, "echo": text},
        )
    )
    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=TwoTurnModel(),
        registry=registry,
    )
    try:
        events = list(instance.run_stream("Echo hello."))
    finally:
        instance.close()

    assert [event["type"] for event in events].count("model_response") == 2
    assert any(
        event.get("type") == "tool_result"
        and event.get("name") == "echo"
        and event.get("result", {}).get("echo") == "hello"
        for event in events
    )
    assert events[-1] == {
        "type": "done",
        "task_status": "completed",
        "termination_reason": "model_returned_no_tool_call",
        "final_text": "Done.",
        "turns": 2,
    }


def test_completed_task_consolidates_before_task_notes_expire() -> None:
    class FinalModel:
        def call(self, context, tools=None):
            del context, tools
            return ModelResponse(text="Done.")

    memory = _TrackingMemory()
    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=FinalModel(),
        memory=memory,
    )
    try:
        events = list(instance.run_stream("Finish normally."))
    finally:
        instance.close()

    assert memory.consolidation_calls == 1
    assert memory.task_notes.snapshot().goal == ""
    assert events[-2]["type"] == "memory_consolidation"
    assert events[-1]["task_status"] == "completed"


def test_turn_budget_abort_skips_memory_consolidation() -> None:
    class ToolCallingModel:
        def call(self, context, tools=None):
            del context, tools
            return ModelResponse(
                tool_calls=[ToolCall(id="call-1", name="echo", arguments={})]
            )

    registry = ToolRegistry()
    registry.register(
        BuiltinTool(
            name="echo",
            description="Return success.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            fn=lambda: {"success": True},
        )
    )
    memory = _TrackingMemory()
    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=ToolCallingModel(),
        memory=memory,
        registry=registry,
    )
    try:
        events = list(instance.run_stream("Keep using the tool.", max_turns=1))
    finally:
        instance.close()

    consolidation = events[-2]
    assert memory.consolidation_calls == 0
    assert memory.task_notes.snapshot().goal == ""
    assert consolidation["type"] == "memory_consolidation"
    assert consolidation["skipped"] is True
    assert consolidation["skip_reason"] == "turn_budget_exhausted"
    assert events[-1]["task_status"] == "aborted"


def test_model_failure_skips_memory_consolidation() -> None:
    class FailingModel:
        def call(self, context, tools=None):
            del context, tools
            raise ModelCallError("test-provider", 1, RuntimeError("offline"))

    memory = _TrackingMemory()
    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=FailingModel(),
        memory=memory,
    )
    try:
        events = list(instance.run_stream("Attempt one decision."))
    finally:
        instance.close()

    consolidation = events[-2]
    assert memory.consolidation_calls == 0
    assert memory.task_notes.snapshot().goal == ""
    assert consolidation["type"] == "memory_consolidation"
    assert consolidation["skipped"] is True
    assert consolidation["skip_reason"] == "model_call_failed"
    assert events[-1]["task_status"] == "aborted"


def test_each_turn_executes_at_most_one_model_selected_tool() -> None:
    calls = {"first": 0, "second": 0}

    class Model:
        turn = 0

        def call(self, context, tools=None):
            del context, tools
            self.turn += 1
            if self.turn == 1:
                return ModelResponse(
                    tool_calls=[
                        ToolCall(id="call-1", name="first", arguments={}),
                        ToolCall(id="call-2", name="second", arguments={}),
                    ]
                )
            return ModelResponse(text="Done.")

    registry = ToolRegistry()
    for name in calls:
        registry.register(
            BuiltinTool(
                name=name,
                description=f"Execute {name}.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                fn=lambda selected=name: (
                    calls.__setitem__(selected, calls[selected] + 1)
                    or {"success": True, "selected": selected}
                ),
            )
        )
    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=Model(),
        registry=registry,
    )
    try:
        events = list(instance.run_stream("Choose one tool at a time."))
    finally:
        instance.close()

    assert calls == {"first": 1, "second": 0}
    assert any(
        event.get("id") == "call-2"
        and event.get("result", {}).get("kind") == "one_tool_per_turn"
        and event.get("executed") is False
        for event in events
    )


def test_callable_tool_registration_infers_public_definition() -> None:
    registry = ToolRegistry()

    @registry.tool(
        post_condition="The requested distance has been traversed.",
    )
    def move(distance_m: float, direction: str = "forward") -> dict:
        """Move the simulated base."""
        return {
            "success": True,
            "distance_m": distance_m,
            "direction": direction,
        }

    definition = registry.list_tool_definitions()[0]

    assert move(0.2)["success"] is True
    assert definition == {
        "name": "move",
        "description": "Move the simulated base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "distance_m": {"type": "number"},
                "direction": {"type": "string", "default": "forward"},
            },
            "required": ["distance_m"],
            "additionalProperties": False,
        },
    }
    assert registry.post_condition("move") == (
        "The requested distance has been traversed."
    )


def test_owned_resources_close_in_reverse_order_and_only_once() -> None:
    closed: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    class Model:
        def call(self, context, tools=None):
            del context, tools
            return ModelResponse(text="Done.")

    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=Model(),
        owned_resources=(Resource("first"), Resource("second")),
    )

    instance.close()
    instance.close()

    assert closed == ["second", "first"]


def test_assembly_failure_closes_owned_resources_in_reverse_order() -> None:
    closed: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    def fail_registration(_registry: ToolRegistry) -> None:
        raise RuntimeError("deployment registration failed")

    with pytest.raises(RuntimeError, match="deployment registration failed"):
        Harness(
            {
                "servers": [],
                "context": {"compaction": {"enabled": False}},
            },
            model=object(),
            builtin_registrar=fail_registration,
            owned_resources=(Resource("first"), Resource("second")),
        )

    assert closed == ["second", "first"]


def test_configuration_failure_still_closes_owned_resources() -> None:
    closed: list[str] = []

    class Resource:
        def close(self) -> None:
            closed.append("closed")

    with pytest.raises(harness.ConfigurationError, match="servers"):
        Harness(
            {"servers": "not-a-list"},
            model=object(),
            owned_resources=(Resource(),),
        )

    assert closed == ["closed"]


def test_source_has_no_private_or_channel_imports() -> None:
    package_root = Path(harness.__file__).resolve().parent
    forbidden_roots = {
        "dashboard",
        "lark",
        "memory",
        "observation",
        "scene_graph",
        "thea_runtime",
        "tools",
    }
    imported_roots: set[str] = set()

    for path in package_root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.partition(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])

    assert forbidden_roots.isdisjoint(imported_roots)
