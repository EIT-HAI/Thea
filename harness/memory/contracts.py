"""Public contracts for Task Notes, Memory, and Tool Experience."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

MemorySection = Literal["preferences", "conventions", "general_lessons"]
ToolExperienceOutcome = Literal["success", "failure"]


@dataclass(frozen=True)
class TaskNotesSnapshot:
    """Completed or in-progress Task Notes supplied to consolidation."""

    goal: str
    phase: str
    timeline: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DurableMemoryEntry:
    """One accepted cross-task entry destined for ``MEMORY.md``."""

    section: MemorySection
    text: str
    confidence: float = 1.0


@dataclass(frozen=True)
class ToolExperienceEntry:
    """One accepted tool-scoped lesson destined for a tool ledger."""

    tool: str
    outcome: ToolExperienceOutcome
    entry: str
    confidence: float = 1.0

    @property
    def tool_name(self) -> str:
        """Compatibility alias for the explicit tool identity."""
        return self.tool

    @property
    def text(self) -> str:
        """Compatibility alias for the durable lesson text."""
        return self.entry


@dataclass(frozen=True)
class MemoryConsolidation:
    """The two durable outputs extracted from one completed task."""

    memory_entries: tuple[DurableMemoryEntry, ...] = ()
    tool_experience_entries: tuple[ToolExperienceEntry, ...] = ()


@runtime_checkable
class MemoryConsolidatorProtocol(Protocol):
    """Extract and validate durable entries from completed Task Notes."""

    def consolidate(
        self,
        task_notes: TaskNotesSnapshot,
        *,
        allowed_tools: frozenset[str],
    ) -> MemoryConsolidation: ...


@runtime_checkable
class ToolExperienceSummarizerProtocol(Protocol):
    """Render the compact summary appended to one Tool Definition."""

    def summarize(
        self,
        tool_name: str,
        *,
        success: tuple[str, ...],
        failure: tuple[str, ...],
    ) -> str: ...


__all__ = [
    "DurableMemoryEntry",
    "MemoryConsolidation",
    "MemoryConsolidatorProtocol",
    "MemorySection",
    "TaskNotesSnapshot",
    "ToolExperienceEntry",
    "ToolExperienceOutcome",
    "ToolExperienceSummarizerProtocol",
]
