from __future__ import annotations

from typing import Any

import pytest
from harness.configuration.runtime import ConfigurationError, validate_runtime_config
from harness.context import (
    TASK_NOTES_MESSAGE_KIND,
    Context,
    ModelResponse,
    ToolCall,
)
from harness.context.compaction import (
    COMPACTION_SUMMARY_MESSAGE_KIND,
    HARNESS_COMPACTION_SOURCE,
    CompactionConfig,
    ContextCompactor,
)
from harness.memory.store import FileMemory
from harness.runtime.task import clear_task_scope


class RecordingSummaryModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def call(
        self,
        context: Context,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        assert tools == []
        prompt = str(context.accumulated_messages[-1]["content"])
        self.prompts.append(prompt)
        return ModelResponse(
            text=(
                "## Objective and Constraints\n"
                "- Preserve the active instruction.\n"
                "## Completed and In Progress\n"
                "- Earlier turns were checkpointed.\n"
                "## Tool Evidence\n"
                "- Tool evidence remains historical.\n"
                "## Open Work\n"
                "- Continue from the recent turns."
            ),
            stop_reason="end_turn",
        )


class MalformedSummaryModel:
    def __init__(self) -> None:
        self.calls = 0

    def call(
        self,
        context: Context,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        del context
        assert tools == []
        self.calls += 1
        return ModelResponse(text="An unstructured summary.", stop_reason="end_turn")


def _add_tool_turn(
    context: Context,
    *,
    index: int,
    user_text: str,
) -> None:
    context.add_user_message(user_text)
    context.add_model_response(
        ModelResponse(
            text=f"turn {index}",
            tool_calls=[
                ToolCall(
                    id=f"call-{index}",
                    name="inspect",
                    arguments={"target": f"object_{index}"},
                )
            ],
            stop_reason="tool_calls",
        )
    )
    context.add_tool_result(
        f"call-{index}",
        {
            "success": True,
            "observation": f"inspection {index} completed",
        },
    )


def test_compaction_replaces_only_old_accumulated_prefix() -> None:
    context = Context()
    context.set_context_layers(
        resident="## System Prompt\nresident",
        refreshed="## Scene Graph Brief\nrefreshed",
    )
    context.set_tool_definitions(
        [
            {
                "name": "inspect",
                "description": "Inspect one object.",
                "inputSchema": {"type": "object"},
            }
        ]
    )
    _add_tool_turn(
        context,
        index=1,
        user_text="first instruction " * 100,
    )
    _add_tool_turn(context, index=2, user_text="second instruction")
    task_notes_content = "Task Notes snapshot:\n- Current phase: inspect"
    context.set_accumulated_message_by_kind(
        {
            "role": "user",
            "content": task_notes_content,
        },
        kind=TASK_NOTES_MESSAGE_KIND,
        source="task_notes",
    )
    context.set_observation_messages(
        [{"role": "user", "content": "current Observation"}]
    )
    context.add_transient_tool_result_message(
        {"role": "user", "content": "transient image evidence"}
    )

    model = RecordingSummaryModel()
    compactor = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
        )
    )

    resident = context.resident_context
    refreshed = context.refreshed_context
    observation = list(context.observation_messages)
    transient = list(context.transient_tool_result_messages)
    event = compactor.maybe_compact(context, model)

    assert event is not None
    assert event["status"] == "completed"
    assert event["summary_rounds"] == 1
    assert context.resident_context == resident
    assert context.refreshed_context == refreshed
    assert context.observation_messages == observation
    assert context.transient_tool_result_messages == transient

    checkpoint = context.accumulated_messages[0]
    assert checkpoint["kind"] == COMPACTION_SUMMARY_MESSAGE_KIND
    assert checkpoint["source"] == HARNESS_COMPACTION_SOURCE
    assert "Compacted Accumulated Messages" in checkpoint["content"]
    retained_task_notes = [
        message
        for message in context.accumulated_messages
        if message.get("kind") == TASK_NOTES_MESSAGE_KIND
    ]
    assert len(retained_task_notes) == 1
    assert retained_task_notes[0]["content"] == task_notes_content
    assert all(task_notes_content not in prompt for prompt in model.prompts)
    assert task_notes_content not in checkpoint["content"]
    assert any(
        message.get("tool_call_id") == "call-2"
        for message in context.accumulated_messages
    )
    assert not any(
        message.get("tool_call_id") == "call-1"
        for message in context.accumulated_messages
    )


def test_task_notes_expire_after_task_end_even_after_compaction(tmp_path) -> None:
    memory = FileMemory(tmp_path)
    memory.task_notes.reset("Inspect the object.")

    context = Context()
    context.set_context_layers(
        resident="## System Prompt\nresident",
        refreshed="## Scene Graph Brief\nrefreshed",
    )
    _add_tool_turn(
        context,
        index=1,
        user_text="old accumulated context " * 100,
    )
    _add_tool_turn(context, index=2, user_text="recent instruction")
    task_notes_message = memory.task_notes.snapshot_message_if_changed()
    assert task_notes_message is not None
    context.set_accumulated_message_by_kind(
        task_notes_message,
        kind=TASK_NOTES_MESSAGE_KIND,
        source="task_notes",
    )

    event = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
        )
    ).maybe_compact(context, RecordingSummaryModel())
    assert event is not None
    assert event["status"] == "completed"

    clear_task_scope(
        memory=memory,
        context=context,
        resident_context=lambda: "## System Prompt\nresident",
    )

    assert memory.task_notes.goal == ""
    assert memory.task_notes.timeline == []
    assert not any(
        message.get("kind") == TASK_NOTES_MESSAGE_KIND
        for message in context.accumulated_messages
    )
    assert (tmp_path / "TASK_NOTES.md").read_text(encoding="utf-8") == ""


def test_oversized_compaction_input_is_summarized_in_multiple_rounds() -> None:
    context = Context()
    context.set_context_layers(resident="resident", refreshed="refreshed")
    first_marker = "FIRST-MIDDLE-MARKER"
    second_marker = "SECOND-MIDDLE-MARKER"
    _add_tool_turn(
        context,
        index=1,
        user_text=("a" * 5_500) + first_marker + ("b" * 5_500),
    )
    _add_tool_turn(
        context,
        index=2,
        user_text=("c" * 5_500) + second_marker + ("d" * 5_500),
    )
    _add_tool_turn(context, index=3, user_text="recent instruction")

    model = RecordingSummaryModel()
    compactor = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
            max_input_chars=10_000,
            max_summary_rounds=16,
        )
    )

    event = compactor.maybe_compact(context, model)

    assert event is not None
    assert event["status"] == "completed"
    assert event["summary_rounds"] > 1
    assert len(model.prompts) == event["summary_rounds"]
    all_prompts = "\n".join(model.prompts)
    assert first_marker in all_prompts
    assert second_marker in all_prompts
    assert "Middle of compaction input omitted" not in all_prompts
    assert "<previous-checkpoint>" in model.prompts[-1]


def test_repeated_compaction_updates_the_existing_checkpoint() -> None:
    context = Context()
    context.set_context_layers(resident="resident", refreshed="refreshed")
    _add_tool_turn(
        context,
        index=1,
        user_text="first instruction " * 100,
    )
    _add_tool_turn(
        context,
        index=2,
        user_text="second instruction " * 100,
    )

    model = RecordingSummaryModel()
    compactor = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
        )
    )
    first = compactor.maybe_compact(context, model)
    assert first is not None
    assert first["status"] == "completed"

    _add_tool_turn(context, index=3, user_text="third instruction")
    prompt_count_before = len(model.prompts)
    second = compactor.maybe_compact(context, model)

    assert second is not None
    assert second["status"] == "completed"
    second_prompts = model.prompts[prompt_count_before:]
    assert second_prompts
    assert "<previous-checkpoint>" in second_prompts[0]
    new_messages = second_prompts[0].split(
        "<new-accumulated-messages>",
        maxsplit=1,
    )[1]
    assert "Compacted Accumulated Messages" not in new_messages


def test_compaction_fails_closed_when_sanitizer_returns_the_wrong_type() -> None:
    context = Context()
    context.set_context_layers(resident="resident", refreshed="refreshed")
    _add_tool_turn(context, index=1, user_text="secret instruction")
    _add_tool_turn(context, index=2, user_text="recent instruction")
    original_messages = list(context.accumulated_messages)
    model = RecordingSummaryModel()
    compactor = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
        ),
        sanitizer=lambda _messages: {"unexpected": "shape"},
    )

    event = compactor.maybe_compact(context, model)

    assert event is not None
    assert event["status"] == "failed"
    assert "sanitizer must return a message list" in event["error"]
    assert context.accumulated_messages == original_messages
    assert model.prompts == []


def test_compaction_bounds_tool_results_and_reasoning_in_summary_input() -> None:
    context = Context()
    context.set_context_layers(resident="resident", refreshed="refreshed")
    context.add_user_message("old instruction")
    context.add_model_response(
        ModelResponse(
            text="I will inspect the target.",
            reasoning_content="REASONING-START" + ("r" * 3_000) + "REASONING-END",
            tool_calls=[
                ToolCall(
                    id="call-old",
                    name="inspect",
                    arguments={"target": "bottle_1"},
                )
            ],
            stop_reason="tool_calls",
        )
    )
    context.add_tool_result(
        "call-old",
        {
            "status": "RESULT-START" + ("x" * 6_000) + "RESULT-END",
        },
    )
    _add_tool_turn(context, index=2, user_text="recent instruction")

    model = RecordingSummaryModel()
    compactor = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
            tool_result_max_chars=400,
            reasoning_max_chars=300,
        )
    )

    event = compactor.maybe_compact(context, model)

    assert event is not None
    assert event["status"] == "completed"
    transcript = "\n".join(model.prompts)
    assert "RESULT-START" in transcript
    assert "RESULT-END" in transcript
    assert "tool result characters omitted" in transcript
    assert "REASONING-START" in transcript
    assert "REASONING-END" in transcript
    assert "reasoning characters omitted" in transcript


def test_malformed_summary_preserves_history_and_does_not_disable_retries() -> None:
    context = Context()
    context.set_context_layers(resident="resident", refreshed="refreshed")
    _add_tool_turn(context, index=1, user_text="old instruction")
    _add_tool_turn(context, index=2, user_text="recent instruction")
    original_messages = list(context.accumulated_messages)
    model = MalformedSummaryModel()
    compactor = ContextCompactor(
        CompactionConfig(
            context_window=2,
            reserve_tokens=1,
            keep_recent_turns=1,
            keep_recent_tokens=8_000,
        )
    )

    events = [compactor.maybe_compact(context, model) for _ in range(4)]

    assert all(event is not None for event in events)
    assert all(event["status"] == "failed" for event in events if event is not None)
    assert all(not event["disabled"] for event in events if event is not None)
    assert context.accumulated_messages == original_messages
    assert model.calls == 4


def test_compaction_config_reserves_less_than_context_window() -> None:
    with pytest.raises(ConfigurationError, match="smaller than context_window"):
        validate_runtime_config(
            {
                "context": {
                    "compaction": {
                        "context_window": 10_000,
                        "reserve_tokens": 10_000,
                    }
                }
            }
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("enabled", "yes"),
        ("context_window", 0),
        ("reserve_tokens", 0),
        ("keep_recent_turns", False),
        ("keep_recent_tokens", -1),
        ("tool_result_max_chars", 0),
        ("reasoning_max_chars", 0),
        ("max_input_chars", 0),
        ("max_summary_rounds", 0),
    ],
)
def test_compaction_config_rejects_invalid_values(
    key: str,
    value: Any,
) -> None:
    with pytest.raises(ConfigurationError, match=key):
        validate_runtime_config(
            {
                "context": {
                    "compaction": {
                        key: value,
                    }
                }
            }
        )
