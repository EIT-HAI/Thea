from __future__ import annotations

from typing import Any

from harness import BuiltinTool, Harness, ModelResponse
from harness.context import Context, ObservationSink, ToolCall
from harness.tools.registry import ToolRegistry
from harness.world.observation import BaseClearance
from harness.world.safety import BaseMotionSafetyFilter


def _clearance(
    *,
    forward: float = 1.0,
    backward: float = 1.0,
    left: float = 1.0,
    right: float = 1.0,
) -> BaseClearance:
    return BaseClearance(
        forward_m=forward,
        backward_m=backward,
        left_m=left,
        right_m=right,
    )


class _Provider:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> BaseClearance:
        self.calls += 1
        return self.result


def _apply(
    safety_filter: BaseMotionSafetyFilter,
    call: ToolCall,
    *,
    turn: int = 1,
):
    return safety_filter.apply(
        call,
        registry=ToolRegistry(),
        turn=turn,
    )


def test_move_base_translation_uses_fresh_clearance_and_is_clamped() -> None:
    provider = _Provider(_clearance(forward=0.4))
    call = ToolCall(
        "call-1",
        "move_base",
        {"mode": "execute", "direction": "forward", "distance_m": 0.8},
    )

    outcome = _apply(
        BaseMotionSafetyFilter(
            clearance_provider=provider,
            margin_m=0.05,
        ),
        call,
        turn=2,
    )

    assert provider.calls == 1
    assert outcome.blocked_result is None
    assert outcome.call.arguments["distance_m"] == 0.35
    assert outcome.events[0]["type"] == "safety_check"
    assert outcome.events[0]["phase"] == "before_execution"
    assert outcome.events[0]["source"] == "base_clearance_provider"
    assert outcome.events[0]["pre_execution_hook"] is True
    assert outcome.events[0]["rewritten_arguments"]["distance_m"] == 0.35


def test_safety_filter_puts_clearance_into_refreshed_observation() -> None:
    provider = _Provider(_clearance(forward=0.6))
    registry = ToolRegistry()
    registry.register(
        BuiltinTool(
            name="move_base",
            description="Move the base.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            fn=lambda: {"success": True},
        )
    )
    context = Context()

    event = BaseMotionSafetyFilter(
        clearance_provider=provider,
    ).refresh_decision_context(
        ObservationSink(context),
        registry=registry,
        turn=3,
    )

    assert provider.calls == 1
    assert event is not None
    assert event["phase"] == "before_model_decision"
    assert event["context_lifetime"] == "refreshed"
    assert event["base_clearance"]["forward_m"] == 0.6
    assert "forward=0.6 m" in str(context.observation_messages)


def test_harness_refreshes_clearance_before_model_decision() -> None:
    provider = _Provider(_clearance(left=0.45))
    registry = ToolRegistry()
    registry.register(
        BuiltinTool(
            name="navigate_to",
            description="Navigate to a target.",
            input_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            },
            fn=lambda target: {"success": True, "target": target},
        )
    )

    class Model:
        def call(self, context, tools=None):
            del tools
            assert "left=0.45 m" in str(context.observation_messages)
            return ModelResponse(text="Done.")

    instance = Harness(
        {
            "servers": [],
            "context": {"compaction": {"enabled": False}},
        },
        model=Model(),
        registry=registry,
        base_clearance_provider=provider,
    )
    try:
        events = list(instance.run_stream("Inspect current clearance."))
    finally:
        instance.close()

    assert provider.calls == 1
    assert any(
        event.get("type") == "safety_check"
        and event.get("phase") == "before_model_decision"
        for event in events
    )


def test_invalid_clearance_provider_result_blocks_navigation() -> None:
    provider = _Provider({"forward_m": 1.0})

    outcome = _apply(
        BaseMotionSafetyFilter(clearance_provider=provider),
        ToolCall("call-1", "navigate_to", {"target": "cup_2"}),
    )

    assert provider.calls == 1
    assert outcome.blocked_result is not None
    assert "returned an invalid value" in outcome.blocked_result["reason"]


def test_navigation_is_not_blocked_by_an_unrelated_direction() -> None:
    provider = _Provider(_clearance(right=0.01))
    call = ToolCall("call-1", "navigate_to", {"target": "cup_2"})

    outcome = _apply(
        BaseMotionSafetyFilter(
            clearance_provider=provider,
            margin_m=0.05,
        ),
        call,
    )

    assert outcome.blocked_result is None
    assert outcome.call == call


def test_navigation_is_blocked_when_no_initial_direction_is_clear() -> None:
    provider = _Provider(_clearance(forward=0.01, backward=0.02, left=0.01, right=0.02))

    outcome = _apply(
        BaseMotionSafetyFilter(
            clearance_provider=provider,
            margin_m=0.05,
        ),
        ToolCall("call-1", "navigate_to", {"target": "cup_2"}),
    )

    assert outcome.blocked_result is not None
    assert (
        "no direction has enough immediate base clearance"
        in outcome.blocked_result["reason"]
    )


def test_nonexecuting_motion_does_not_refresh_clearance() -> None:
    provider = _Provider(_clearance())
    call = ToolCall(
        "call-1",
        "move_base",
        {"mode": "check_only", "direction": "forward", "distance_m": 0.5},
    )

    outcome = _apply(
        BaseMotionSafetyFilter(clearance_provider=provider),
        call,
    )

    assert provider.calls == 0
    assert outcome == outcome.__class__(call)


def test_move_base_without_mode_fails_safe_and_refreshes_clearance() -> None:
    provider = _Provider(_clearance(forward=0.4))
    call = ToolCall(
        "call-1",
        "move_base",
        {"direction": "forward", "distance_m": 0.8},
    )

    outcome = _apply(
        BaseMotionSafetyFilter(
            clearance_provider=provider,
            margin_m=0.05,
        ),
        call,
        turn=2,
    )

    assert provider.calls == 1
    assert outcome.blocked_result is None
    assert outcome.call.arguments["distance_m"] == 0.35


def test_executable_motion_without_clearance_provider_is_blocked() -> None:
    outcome = _apply(
        BaseMotionSafetyFilter(),
        ToolCall("call-1", "navigate_to", {"target": "cup_2"}),
    )

    assert outcome.blocked_result is not None
    assert "No BaseClearance provider is installed" in outcome.blocked_result["reason"]
