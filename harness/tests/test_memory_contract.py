from __future__ import annotations

from pathlib import Path

import pytest
from harness.context import ModelResponse
from harness.memory.store import (
    DurableMemoryEntry,
    FileMemory,
    MemoryConsolidation,
    ModelMemoryConsolidator,
    ModelToolExperienceSummarizer,
    TaskNotesSnapshot,
    ToolExperienceEntry,
)


class _Consolidator:
    def __init__(self) -> None:
        self.snapshots: list[TaskNotesSnapshot] = []

    def consolidate(
        self,
        task_notes: TaskNotesSnapshot,
        *,
        allowed_tools: frozenset[str],
    ) -> MemoryConsolidation:
        self.snapshots.append(task_notes)
        assert allowed_tools == frozenset({"pick_object"})
        return MemoryConsolidation(
            memory_entries=(
                DurableMemoryEntry(
                    "preferences",
                    "The user prefers concise completion reports.",
                ),
                DurableMemoryEntry(
                    "conventions",
                    "Ask before choosing among equally plausible destinations.",
                ),
                DurableMemoryEntry(
                    "general_lessons",
                    "Use current physical evidence before a local action.",
                ),
            ),
            tool_experience_entries=(
                ToolExperienceEntry(
                    "pick_object",
                    "success",
                    "Center the target in the current manipulation view.",
                ),
                ToolExperienceEntry(
                    "pick_object",
                    "failure",
                    "A stale image does not establish current grasp readiness.",
                ),
            ),
        )


class _Summarizer:
    def summarize(
        self,
        tool_name: str,
        *,
        success: tuple[str, ...],
        failure: tuple[str, ...],
    ) -> str:
        return (
            f"{tool_name}: {len(success)} confirmed success lesson; "
            f"{len(failure)} confirmed failure lesson."
        )


def test_task_notes_surface_only_changed_snapshots(tmp_path: Path) -> None:
    memory = FileMemory(tmp_path, mirror_task_notes=True)
    memory.task_notes.reset("Pick up the cup.")

    first = memory.task_notes.snapshot_message_if_changed()
    assert first is not None
    assert "Goal: Pick up the cup." in first["content"]
    assert memory.task_notes.snapshot_message_if_changed() is None

    memory.task_notes.append_tool_result(
        tool="pick_object",
        arguments={"target": "cup_2"},
        result={"success": True, "run_id": "run-1"},
        evaluator_verdict={
            "status": "success",
            "evidence": ["cup is held"],
            "failure_reason": "",
        },
    )
    changed = memory.task_notes.snapshot_message_if_changed()
    assert changed is not None
    assert "pick_object: success" in changed["content"]
    assert (tmp_path / "TASK_NOTES.md").is_file()

    memory.task_notes.expire()
    assert (tmp_path / "TASK_NOTES.md").read_text(encoding="utf-8") == ""


def test_file_memory_writes_both_durable_stores(tmp_path: Path) -> None:
    consolidator = _Consolidator()
    memory = FileMemory(
        tmp_path,
        consolidator=consolidator,
        tool_experience_summarizer=_Summarizer(),
    )
    memory.task_notes.reset("Pick up the cup.")
    memory.task_notes.append_tool_result(
        tool="pick_object",
        arguments={"target": "cup_2"},
        result={"success": False, "reason": "too far", "run_id": "run-1"},
        evaluator_verdict={
            "status": "failure",
            "evidence": ["cup remained on table"],
            "failure_reason": "too far",
        },
    )
    memory.task_notes.append_tool_result(
        tool="pick_object",
        arguments={"target": "cup_2"},
        result={"success": True, "run_id": "run-2"},
        evaluator_verdict={
            "status": "success",
            "evidence": ["cup is held"],
            "failure_reason": "",
        },
    )

    event = memory.consolidate(allowed_tools={"pick_object"})

    assert event["memory_written"] == 3
    assert event["tool_experience_written"] == 2
    assert consolidator.snapshots[0].goal == "Pick up the cup."
    resident = memory.resident_memory()
    assert "## Preferences" in resident
    assert "## Conventions" in resident
    assert "## General Lessons" in resident
    assert "[user-preference] The user prefers concise completion reports." in resident
    assert "[convention] Ask before choosing" in resident
    assert "[general-lesson] Use current physical evidence" in resident
    ledger = (tmp_path / "tool_experience" / "pick_object.md").read_text(
        encoding="utf-8"
    )
    assert "## Success" in ledger
    assert "## Failure" in ledger
    assert "## Notes" not in ledger
    assert memory.tool_experience_summaries({"pick_object"}) == {
        "pick_object": (
            "pick_object: 1 confirmed success lesson; 1 confirmed failure lesson."
        )
    }

    lesson = ToolExperienceEntry(
        "pick_object",
        "success",
        "Center the target in the current manipulation view.",
    )
    assert lesson.tool == lesson.tool_name == "pick_object"
    assert lesson.entry == lesson.text


def test_file_memory_deduplicates_entries(tmp_path: Path) -> None:
    memory = FileMemory(tmp_path, consolidator=_Consolidator())
    memory.task_notes.reset("Pick up the cup.")
    memory.task_notes.append_tool_result(
        tool="pick_object",
        result={"success": False, "reason": "too far", "run_id": "run-1"},
        evaluator_verdict={
            "status": "failure",
            "failure_reason": "too far",
        },
    )
    memory.task_notes.append_tool_result(
        tool="pick_object",
        result={"success": True, "run_id": "run-2"},
        evaluator_verdict={"status": "success"},
    )

    first = memory.consolidate(allowed_tools={"pick_object"})
    second = memory.consolidate(allowed_tools={"pick_object"})

    assert first["memory_written"] == 3
    assert first["tool_experience_written"] == 2
    assert second["memory_written"] == 0
    assert second["tool_experience_written"] == 0


def test_resident_memory_projects_only_paper_defined_sections(
    tmp_path: Path,
) -> None:
    (tmp_path / "MEMORY.md").write_text(
        """# Memory

## Preferences
- Use concise reports.

## Private Operator Notes
- This text must not enter model context.

## Conventions
- Ask before choosing among equally plausible destinations.

## General Lessons
- Use current physical evidence before local action.
""",
        encoding="utf-8",
    )
    memory = FileMemory(tmp_path)

    resident = memory.resident_memory()

    assert "## Preferences" in resident
    assert "## Conventions" in resident
    assert "## General Lessons" in resident
    assert "Private Operator Notes" not in resident
    assert "must not enter model context" not in resident


def test_file_memory_rejects_tool_experience_for_unknown_tool(
    tmp_path: Path,
) -> None:
    class UnknownToolConsolidator:
        def consolidate(self, task_notes, *, allowed_tools):
            del task_notes, allowed_tools
            return MemoryConsolidation(
                tool_experience_entries=(
                    ToolExperienceEntry("unknown_tool", "failure", "Do not retry."),
                )
            )

    memory = FileMemory(tmp_path, consolidator=UnknownToolConsolidator())
    memory.task_notes.reset("Run a task.")

    with pytest.raises(ValueError, match="unregistered tool"):
        memory.consolidate(allowed_tools={"pick_object"})


def test_file_memory_requires_confidence_and_trajectory_support(
    tmp_path: Path,
) -> None:
    class GuardedConsolidator:
        def consolidate(self, task_notes, *, allowed_tools):
            del task_notes, allowed_tools
            return MemoryConsolidation(
                memory_entries=(
                    DurableMemoryEntry(
                        "preferences",
                        "The user prefers short reports.",
                        confidence=0.5,
                    ),
                    DurableMemoryEntry(
                        "conventions",
                        "Use current cup_2 for this run.",
                        confidence=0.99,
                    ),
                ),
                tool_experience_entries=(
                    ToolExperienceEntry(
                        "pick_object",
                        "failure",
                        "Move closer before retrying.",
                        confidence=0.99,
                    ),
                ),
            )

    memory = FileMemory(tmp_path, consolidator=GuardedConsolidator())
    memory.task_notes.reset("Pick up the cup.")
    memory.task_notes.append_tool_result(
        tool="pick_object",
        result={"success": False, "reason": "too far", "run_id": "run-1"},
        evaluator_verdict={
            "status": "failure",
            "failure_reason": "too far",
        },
    )

    event = memory.consolidate(allowed_tools={"pick_object"})

    assert event["memory_written"] == 0
    assert event["tool_experience_written"] == 0
    assert not (tmp_path / "MEMORY.md").exists()
    assert not (tmp_path / "tool_experience" / "pick_object.md").exists()


def test_failure_lesson_rejects_success_of_another_tool(
    tmp_path: Path,
) -> None:
    class RecoveryConsolidator:
        def consolidate(self, task_notes, *, allowed_tools):
            del task_notes, allowed_tools
            return MemoryConsolidation(
                tool_experience_entries=(
                    ToolExperienceEntry(
                        "pick_object",
                        "failure",
                        "Reposition before retrying when the target is out of reach.",
                    ),
                )
            )

    memory = FileMemory(tmp_path, consolidator=RecoveryConsolidator())
    memory.task_notes.reset("Move the object.")
    memory.task_notes.append_tool_result(
        tool="pick_object",
        result={"success": False, "run_id": "run-1"},
        evaluator_verdict={"status": "failure", "failure_reason": "too far"},
    )
    memory.task_notes.append_tool_result(
        tool="alternate_pick",
        result={"success": True, "run_id": "run-2"},
        evaluator_verdict={"status": "success"},
    )

    event = memory.consolidate(allowed_tools={"pick_object", "alternate_pick"})

    assert event["tool_experience_written"] == 0
    assert not (tmp_path / "tool_experience" / "pick_object.md").exists()


def test_failure_lesson_accepts_later_success_of_the_same_tool(
    tmp_path: Path,
) -> None:
    class RecoveryConsolidator:
        def consolidate(self, task_notes, *, allowed_tools):
            del task_notes, allowed_tools
            return MemoryConsolidation(
                tool_experience_entries=(
                    ToolExperienceEntry(
                        "pick_object",
                        "failure",
                        "Reposition before retrying when the target is out of reach.",
                    ),
                )
            )

    memory = FileMemory(tmp_path, consolidator=RecoveryConsolidator())
    memory.task_notes.reset("Move the object.")
    memory.task_notes.append_tool_result(
        tool="pick_object",
        result={"success": False, "run_id": "run-1"},
        evaluator_verdict={"status": "failure", "failure_reason": "too far"},
    )
    memory.task_notes.append_tool_result(
        tool="pick_object",
        result={"success": True, "run_id": "run-2"},
        evaluator_verdict={"status": "success"},
    )

    event = memory.consolidate(allowed_tools={"pick_object"})

    assert event["tool_experience_written"] == 1
    ledger = (tmp_path / "tool_experience" / "pick_object.md").read_text(
        encoding="utf-8"
    )
    assert "Reposition before retrying" in ledger


def test_file_memory_without_consolidator_is_explicit_noop(tmp_path: Path) -> None:
    memory = FileMemory(tmp_path)
    memory.task_notes.reset("Run a task.")

    event = memory.consolidate(allowed_tools={"pick_object"})

    assert event["success"] is True
    assert event["skipped"] is True
    assert event["memory_written"] == 0
    assert event["tool_experience_written"] == 0


def test_model_backed_consolidation_and_tool_summary() -> None:
    class ReplayModel:
        def __init__(self, responses: list[str]) -> None:
            self.responses = list(responses)
            self.calls = []

        def call(self, context, tools=None):
            self.calls.append((context, tools))
            return ModelResponse(text=self.responses.pop(0))

    model = ReplayModel(
        [
            """{
              "memory_entries": [
                {
                  "section": "preferences",
                  "text": "Use Chinese for reports.",
                  "confidence": 0.98
                }
              ],
              "tool_experience_entries": [
                {
                  "tool_name": "pick_object",
                  "outcome": "failure",
                  "text": "Re-observe if alignment evidence is stale.",
                  "confidence": 0.91
                }
              ]
            }""",
            "Re-observe before calling the tool when alignment evidence is stale.",
        ]
    )
    consolidation = ModelMemoryConsolidator(model).consolidate(
        TaskNotesSnapshot(
            goal="Pick up the cup.",
            phase="completed",
            timeline=({"event": "task_start"},),
        ),
        allowed_tools=frozenset({"pick_object"}),
    )
    summary = ModelToolExperienceSummarizer(model).summarize(
        "pick_object",
        success=(),
        failure=("Re-observe if alignment evidence is stale.",),
    )

    assert consolidation.memory_entries[0].section == "preferences"
    assert consolidation.tool_experience_entries[0].tool_name == "pick_object"
    assert "Re-observe" in summary
    assert model.calls[0][1] == []


def test_model_consolidator_automatically_summarizes_tool_experience(
    tmp_path: Path,
) -> None:
    class ReplayModel:
        def __init__(self) -> None:
            self.calls = []

        def call(self, context, tools=None):
            self.calls.append((context, tools))
            return ModelResponse(
                text=(
                    "Center the target before calling pick_object and "
                    "re-observe when alignment evidence is stale."
                )
            )

    ledger = tmp_path / "tool_experience" / "pick_object.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        """# Tool Experience: pick_object

## Success
- Center the target in the manipulation view.

## Failure
- Re-observe when alignment evidence is stale.
""",
        encoding="utf-8",
    )
    model = ReplayModel()
    memory = FileMemory(
        tmp_path,
        consolidator=ModelMemoryConsolidator(model),
    )

    summaries = memory.tool_experience_summaries({"pick_object"})

    assert "Center the target" in summaries["pick_object"]
    assert len(model.calls) == 1
    assert model.calls[0][1] == []


def test_existing_tool_experience_requires_a_configured_summarizer(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "tool_experience" / "pick_object.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        """# Tool Experience: pick_object

## Success
- Center the target before grasping.

## Failure
""",
        encoding="utf-8",
    )
    memory = FileMemory(tmp_path, consolidator=_Consolidator())

    with pytest.raises(RuntimeError, match="no ToolExperienceSummarizerProtocol"):
        memory.tool_experience_summaries({"pick_object"})
