"""No-op boundaries for running the harness without robot integrations.

These implementations satisfy the harness protocols while keeping all state
in memory. They are useful for tests, examples, and deployments that inject
only a model and a tool registry.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from harness.context import ObservationSink
from harness.memory.store import TaskNotes
from harness.world.scene_graph import SceneGraphBrief


class InMemoryTaskNotes(TaskNotes):
    """Task Notes with no operator-facing mirror file."""


class NullMemory:
    """Memory boundary for standalone loops that do not install persistence."""

    def __init__(self) -> None:
        self.task_notes = InMemoryTaskNotes()

    def resident_memory(self) -> str:
        return ""

    def tool_experience_summaries(
        self,
        tool_names: Iterable[str],
    ) -> dict[str, str]:
        del tool_names
        return {}

    def consolidate(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        del allowed_tools
        return {
            "type": "memory_consolidation",
            "success": True,
            "skipped": True,
            "memory_written": 0,
            "tool_experience_written": 0,
        }


class NullSceneGraph:
    """Empty world-state boundary for standalone or non-robot tasks."""

    def refresh_from_perception(self) -> None:
        return None

    def brief(self) -> SceneGraphBrief:
        return SceneGraphBrief()

    def apply_confirmed_execution(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        evaluator_verdict: dict[str, Any],
    ) -> dict[str, Any] | None:
        del tool_name, arguments, result, evaluator_verdict
        return None


def null_observation(
    context: ObservationSink,
    turn: int,
) -> dict[str, Any]:
    """Clear prior Observation and report that no provider is installed."""
    context.clear_observation_messages()
    return {
        "type": "observation",
        "turn": turn,
        "available": False,
        "success": False,
        "reason": "No Observation provider is installed.",
    }


__all__ = [
    "InMemoryTaskNotes",
    "NullMemory",
    "NullSceneGraph",
    "null_observation",
]
