from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from harness import (
    Context,
    EvaluationRequest,
    Harness,
    ModelResponse,
    Observation,
    ObservationSink,
    ToolCall,
    ToolRegistry,
    VisualEvidence,
)

from thea_simulation import (
    LiberoEpisode,
    RoboTwinEpisode,
    SimulationRuntime,
    SimulationTaskEvaluator,
    action_chunk_tool,
    project_libero_observation,
    project_robotwin_observation,
)
from thea_simulation.adapters.libero import _encode_libero_rgb_png


def _project_observation(raw: dict[str, Any], turn: int) -> Observation:
    return Observation(
        visuals=(
            VisualEvidence(
                name="agent",
                url=f"https://example.invalid/frame-{raw['frame']}.png",
                caption="Current simulation camera",
                freshness="current",
            ),
        ),
        captured_at=turn,
        provenance=str(raw["benchmark"]),
    )


class _FakeLiberoEnv:
    def __init__(self) -> None:
        self.frame = 0
        self.closed = False
        self.seed_value: int | None = None

    def seed(self, seed: int) -> None:
        self.seed_value = seed

    def reset(self) -> dict[str, Any]:
        self.frame = 0
        return {"frame": self.frame, "benchmark": "libero"}

    def set_init_state(self, state: dict[str, int]) -> dict[str, Any]:
        self.frame = state["frame"]
        return {"frame": self.frame, "benchmark": "libero"}

    def step(self, action: list[float]):
        del action
        self.frame += 1
        success = self.frame >= 2
        return (
            {"frame": self.frame, "benchmark": "libero"},
            float(success),
            success,
            {"frame": self.frame},
        )

    def check_success(self) -> bool:
        return self.frame >= 2

    def close(self) -> None:
        self.closed = True


class _FakeRgbImage:
    shape = (2, 3, 3)

    def __init__(self, payload: bytes) -> None:
        self.payload = payload


def test_libero_observation_projector_keeps_every_rgb_camera() -> None:
    projected = project_libero_observation(
        {
            "robot0_eye_in_hand_image": _FakeRgbImage(b"wrist-png"),
            "agentview_image": _FakeRgbImage(b"agent-png"),
            "agentview_depth": object(),
            "robot0_joint_pos": object(),
        },
        4,
        image_encoder=lambda image: image.payload,
    )

    assert [visual.name for visual in projected.visuals] == [
        "agentview",
        "robot0_eye_in_hand",
    ]
    assert [visual.data for visual in projected.visuals] == [
        b"agent-png",
        b"wrist-png",
    ]
    assert projected.captured_at == 4
    assert projected.provenance == "libero"
    assert projected.summary == "Current LIBERO observation from 2 camera(s)."


def test_libero_observation_projector_rejects_missing_rgb_cameras() -> None:
    try:
        project_libero_observation(
            {"robot0_joint_pos": object()},
            1,
            image_encoder=lambda image: image.payload,
        )
    except ValueError as exc:
        assert "no HxWx3 *_image fields" in str(exc)
    else:
        raise AssertionError("missing LIBERO camera fields must fail")


def test_robotwin_observation_projector_keeps_every_rgb_camera() -> None:
    projected = project_robotwin_observation(
        {
            "observation": {
                "right_camera": {
                    "rgb": _FakeRgbImage(b"right-png"),
                    "intrinsic_cv": object(),
                },
                "head_camera": {"rgb": _FakeRgbImage(b"head-png")},
                "left_camera": {"rgb": _FakeRgbImage(b"left-png")},
            },
            "third_view_rgb": _FakeRgbImage(b"third-png"),
            "joint_action": {"vector": object()},
        },
        5,
        image_encoder=lambda image: image.payload,
    )

    assert [visual.name for visual in projected.visuals] == [
        "head_camera",
        "left_camera",
        "right_camera",
        "third_view",
    ]
    assert [visual.data for visual in projected.visuals] == [
        b"head-png",
        b"left-png",
        b"right-png",
        b"third-png",
    ]
    assert projected.captured_at == 5
    assert projected.provenance == "robotwin"
    assert projected.summary == "Current RoboTwin observation from 4 camera(s)."


def test_robotwin_observation_projector_rejects_missing_rgb_cameras() -> None:
    try:
        project_robotwin_observation(
            {"observation": {"head_camera": {"depth": object()}}},
            1,
            image_encoder=lambda image: image.payload,
        )
    except ValueError as exc:
        assert "no HxWx3 RGB camera fields" in str(exc)
    else:
        raise AssertionError("missing RoboTwin camera fields must fail")


def test_libero_default_encoder_only_flips_the_vertical_axis(
    monkeypatch,
) -> None:
    class Encoded:
        def tobytes(self) -> bytes:
            return b"png"

    fake_cv2 = SimpleNamespace(
        COLOR_RGB2BGR=1,
        cvtColor=lambda value, code: ("bgr", value, code),
        imencode=lambda suffix, value: (
            suffix == ".png" and value == ("bgr", "vertically-flipped-rgb", 1),
            Encoded(),
        ),
    )

    class Array:
        ndim = 3
        shape = (2, 3, 3)

        def __getitem__(self, key):
            assert key == slice(None, None, -1)
            return "vertical-slice"

    fake_np = SimpleNamespace(
        asarray=lambda _value: Array(),
        ascontiguousarray=lambda value: (
            "vertically-flipped-rgb" if value == "vertical-slice" else "unexpected"
        ),
    )

    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "numpy", fake_np)

    assert _encode_libero_rgb_png(object()) == b"png"


class _FakeRoboTwinEnv:
    def __init__(self) -> None:
        self.frame = 0
        self.take_action_cnt = 0
        self.step_lim = 3
        self.eval_success = False
        self.instruction = ""
        self.closed = False

    def set_instruction(self, *, instruction: str) -> None:
        self.instruction = instruction

    def get_obs(self) -> dict[str, Any]:
        return {"frame": self.frame, "benchmark": "robotwin"}

    def take_action(self, action: list[float], *, action_type: str) -> None:
        assert action_type == "qpos"
        del action
        self.frame += 1
        self.take_action_cnt += 1
        self.eval_success = self.frame >= 2

    def check_success(self) -> bool:
        return self.eval_success

    def close_env(self) -> None:
        self.closed = True


def _two_actions(episode, arguments):
    del episode, arguments
    yield [0.0]
    yield [1.0]


def _one_action(episode, arguments):
    del episode, arguments
    yield [0.0]


def _tool(policy):
    return action_chunk_tool(
        name="pick_object",
        description="Execute the benchmark policy for one confirmed target.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        post_condition="The benchmark task success condition is satisfied.",
        policy=policy,
    )


def test_libero_adapter_drives_simulation_runtime_and_evaluator() -> None:
    env = _FakeLiberoEnv()
    episode = LiberoEpisode(
        env,
        task_id="libero_10:0:0",
        instruction="Pick up the object.",
        initial_state={"frame": 0},
    )
    runtime = SimulationRuntime(
        episode,
        observation_projector=_project_observation,
        tools=(_tool(_two_actions),),
    )
    assert runtime.reset(seed=7)["frame"] == 0
    assert env.seed_value == 7

    registry = ToolRegistry()
    runtime.register_tools(registry)
    result = registry.call_tool("pick_object", {"target": "object_1"})
    assert result["success"]
    assert result["actions_executed"] == 2
    assert result["task_success"]

    evidence = runtime.capture(result["run_id"])
    verdict = SimulationTaskEvaluator().evaluate(
        EvaluationRequest(
            post_condition=registry.post_condition("pick_object") or "",
            post_execution_observation=evidence,
        )
    )
    assert verdict.status == "success"

    context = Context()
    event = runtime.observation_provider(
        ObservationSink(context),
        1,
    )
    assert event["available"]
    assert event["provenance"] == "libero"
    assert context.observation_messages

    runtime.close()
    assert env.closed


def test_robotwin_adapter_reports_process_before_task_success() -> None:
    env = _FakeRoboTwinEnv()

    def setup(task_env: _FakeRoboTwinEnv, seed: int | None):
        assert seed == 11
        task_env.frame = 0
        task_env.take_action_cnt = 0
        task_env.eval_success = False
        return task_env.get_obs()

    episode = RoboTwinEpisode(
        env,
        task_id="pick_dual_bottles:demo_clean:11",
        instruction="Pick up both bottles.",
        setup=setup,
    )
    runtime = SimulationRuntime(
        episode,
        observation_projector=_project_observation,
        tools=(_tool(_one_action),),
    )
    runtime.reset(seed=11)
    assert env.instruction == "Pick up both bottles."

    registry = ToolRegistry()
    runtime.register_tools(registry)
    result = registry.call_tool("pick_object", {"target": "bottle_1"})
    evidence = runtime.capture(result["run_id"])
    verdict = SimulationTaskEvaluator().evaluate(
        EvaluationRequest(
            post_condition=registry.post_condition("pick_object") or "",
            post_execution_observation=evidence,
        )
    )
    assert verdict.status == "process"
    assert not evidence.task_success

    runtime.close()
    assert env.closed


def test_robotwin_adapter_runs_through_the_agentic_loop() -> None:
    env = _FakeRoboTwinEnv()

    def setup(task_env: _FakeRoboTwinEnv, seed: int | None):
        del seed
        task_env.frame = 0
        task_env.take_action_cnt = 0
        task_env.eval_success = False
        return task_env.get_obs()

    episode = RoboTwinEpisode(
        env,
        task_id="pick_dual_bottles:demo_clean:0",
        instruction="Pick up both bottles.",
        setup=setup,
    )
    runtime = SimulationRuntime(
        episode,
        observation_projector=_project_observation,
        tools=(_tool(_one_action),),
    )
    runtime.reset(seed=0)
    registry = ToolRegistry()
    runtime.register_tools(registry)

    class TwoStepModel:
        def __init__(self) -> None:
            self.turn = 0

        def call(self, context, tools=None):
            del context, tools
            self.turn += 1
            if self.turn == 1:
                return ModelResponse(
                    text="Continue the benchmark policy.",
                    tool_calls=[
                        ToolCall(
                            id=f"pick-{self.turn}",
                            name="pick_object",
                            arguments={"target": "bottle_1"},
                        )
                    ],
                    stop_reason="tool_use",
                )
            return ModelResponse(
                text="The benchmark task succeeded.",
                stop_reason="stop",
            )

    harness = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
            "evaluation": {
                "required_tools": ["pick_object"],
                "segment_tools": ["pick_object"],
            },
        },
        model=TwoStepModel(),
        registry=registry,
        observation_provider=runtime.observation_provider,
        evaluator=SimulationTaskEvaluator(),
        post_execution_observation_provider=runtime,
        owned_resources=(runtime,),
    )
    try:
        events = list(harness.run_stream(episode.instruction))
    finally:
        harness.close()

    evaluator_results = [
        event["result"]["evaluation"]["status"]
        for event in events
        if event.get("type") == "tool_result" and event.get("name") == "evaluate_run"
    ]
    assert evaluator_results == ["process", "success"]
    assert events[-1]["type"] == "done"
    assert env.closed
