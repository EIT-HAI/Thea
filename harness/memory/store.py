"""File-backed implementation of the paper-defined Memory lifecycle."""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .contracts import (
    DurableMemoryEntry,
    MemoryConsolidation,
    MemoryConsolidatorProtocol,
    MemorySection,
    TaskNotesSnapshot,
    ToolExperienceEntry,
    ToolExperienceOutcome,
    ToolExperienceSummarizerProtocol,
)
from .model import (
    ModelMemoryConsolidator,
    ModelToolExperienceSummarizer,
    confidence,
)
from .task_notes import TaskNotes, atomic_write

_MEMORY_SECTION_TITLES: dict[MemorySection, str] = {
    "preferences": "Preferences",
    "conventions": "Conventions",
    "general_lessons": "General Lessons",
}
_MEMORY_SECTION_TAGS: dict[MemorySection, str] = {
    "preferences": "user-preference",
    "conventions": "convention",
    "general_lessons": "general-lesson",
}
_MEMORY_TAGS_BY_TITLE = {
    _MEMORY_SECTION_TITLES[section]: tag
    for section, tag in _MEMORY_SECTION_TAGS.items()
}
_TOOL_SECTION_TITLES: dict[ToolExperienceOutcome, str] = {
    "success": "Success",
    "failure": "Failure",
}
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_ENTRY_CHARS = 2_000
_MAX_TOOL_EXPERIENCE_SUMMARY_CHARS = 700
_TRANSIENT_PATTERNS = (
    re.compile(r"\b[A-Za-z][A-Za-z0-9-]*_\d+\b"),
    re.compile(r"\b(?:currently|right now|this run|this time|now)\b", re.I),
    re.compile(r"(?:\u5f53\u524d|\u73b0\u5728|\u521a\u521a|\u8fd9\u6b21|\u672c\u6b21)"),
    re.compile(r"data:image|image_url|base64", re.I),
)


class FileMemory:
    """Persist Memory and per-tool experience as inspectable Markdown."""

    def __init__(
        self,
        root: str | Path,
        *,
        consolidator: MemoryConsolidatorProtocol | None = None,
        tool_experience_summarizer: ToolExperienceSummarizerProtocol | None = None,
        mirror_task_notes: bool = True,
        min_confidence: float = 0.75,
    ) -> None:
        self.root = Path(root).expanduser()
        self.memory_path = self.root / "MEMORY.md"
        self.tool_experience_dir = self.root / "tool_experience"
        self.consolidator = consolidator
        self.tool_experience_summarizer = (
            ModelToolExperienceSummarizer(consolidator.model)
            if tool_experience_summarizer is None
            and isinstance(consolidator, ModelMemoryConsolidator)
            else tool_experience_summarizer
        )
        self.min_confidence = _validated_confidence_threshold(min_confidence)
        self.task_notes = TaskNotes(
            self.root / "TASK_NOTES.md" if mirror_task_notes else None
        )
        self._lock = threading.RLock()

    def resident_memory(self) -> str:
        """Render the three durable Memory sections as Resident Context."""
        with self._lock:
            if not self.memory_path.is_file():
                return ""
            sections = _parse_memory_sections(
                self.memory_path.read_text(encoding="utf-8")
            )
            return _render_memory_store(sections).strip()

    def tool_experience_summaries(
        self,
        tool_names: Iterable[str],
    ) -> dict[str, str]:
        """Summarize each requested ledger for its Tool Definition."""
        summaries: dict[str, str] = {}
        with self._lock:
            for tool_name in sorted(_normalized_tool_names(tool_names)):
                path = self._tool_experience_path(tool_name)
                if not path.is_file():
                    continue
                sections = _parse_markdown_sections(path.read_text(encoding="utf-8"))
                success = tuple(sections.get("Success", ()))
                failure = tuple(sections.get("Failure", ()))
                if not success and not failure:
                    continue
                if self.tool_experience_summarizer is None:
                    raise RuntimeError(
                        "Tool Experience exists but no "
                        "ToolExperienceSummarizerProtocol is configured. "
                        "Use ModelMemoryConsolidator for automatic LLM "
                        "summarization or supply an explicit summarizer."
                    )
                summary = self.tool_experience_summarizer.summarize(
                    tool_name,
                    success=success,
                    failure=failure,
                )
                normalized = (
                    str(summary or "")
                    .strip()[:_MAX_TOOL_EXPERIENCE_SUMMARY_CHARS]
                    .rstrip()
                )
                if normalized:
                    summaries[tool_name] = normalized
        return summaries

    def consolidate(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        """Consolidate completed Task Notes into both durable stores."""
        if self.consolidator is None:
            return {
                "type": "memory_consolidation",
                "success": True,
                "skipped": True,
                "reason": "No Memory consolidator is configured.",
                "memory_written": 0,
                "tool_experience_written": 0,
            }

        allowed = frozenset(_normalized_tool_names(allowed_tools))
        consolidation = self.consolidator.consolidate(
            self.task_notes.snapshot(),
            allowed_tools=allowed,
        )
        if not isinstance(consolidation, MemoryConsolidation):
            raise TypeError(
                "Memory consolidator must return MemoryConsolidation, "
                f"got {type(consolidation).__name__}."
            )

        memory_entries = _validated_memory_entries(
            consolidation.memory_entries,
            min_confidence=self.min_confidence,
        )
        tool_entries = _validated_tool_entries(
            consolidation.tool_experience_entries,
            allowed_tools=allowed,
            task_notes=self.task_notes,
            min_confidence=self.min_confidence,
        )
        with self._lock:
            memory_written = self._write_memory_entries(memory_entries)
            tool_written = self._write_tool_entries(tool_entries)
        return {
            "type": "memory_consolidation",
            "success": True,
            "memory_written": memory_written,
            "tool_experience_written": tool_written,
        }

    def _write_memory_entries(
        self,
        entries: tuple[DurableMemoryEntry, ...],
    ) -> int:
        sections = (
            _parse_memory_sections(self.memory_path.read_text(encoding="utf-8"))
            if self.memory_path.is_file()
            else {}
        )
        written = 0
        for entry in entries:
            bucket = sections.setdefault(_MEMORY_SECTION_TITLES[entry.section], [])
            if _append_unique(bucket, entry.text):
                written += 1
        if entries or self.memory_path.is_file():
            atomic_write(
                self.memory_path,
                _render_memory_store(sections),
            )
        return written

    def _write_tool_entries(
        self,
        entries: tuple[ToolExperienceEntry, ...],
    ) -> int:
        grouped: dict[str, list[ToolExperienceEntry]] = {}
        for entry in entries:
            grouped.setdefault(entry.tool, []).append(entry)

        written = 0
        for tool_name, tool_entries in grouped.items():
            path = self._tool_experience_path(tool_name)
            sections = (
                _parse_markdown_sections(path.read_text(encoding="utf-8"))
                if path.is_file()
                else {}
            )
            for entry in tool_entries:
                bucket = sections.setdefault(_TOOL_SECTION_TITLES[entry.outcome], [])
                if _append_unique(bucket, entry.entry):
                    written += 1
            atomic_write(
                path,
                _render_markdown_store(
                    f"Tool Experience: {tool_name}",
                    sections,
                    tuple(_TOOL_SECTION_TITLES.values()),
                ),
            )
        return written

    def _tool_experience_path(self, tool_name: str) -> Path:
        normalized = str(tool_name or "").strip()
        if not _SAFE_TOOL_NAME.fullmatch(normalized):
            raise ValueError(
                "Tool names used for experience files may contain only letters, "
                "digits, dot, underscore, and hyphen."
            )
        return self.tool_experience_dir / f"{normalized}.md"


def _normalized_tool_names(values: Iterable[str]) -> set[str]:
    return {str(value).strip() for value in values if str(value).strip()}


def _validated_memory_entries(
    entries: Iterable[DurableMemoryEntry],
    *,
    min_confidence: float,
) -> tuple[DurableMemoryEntry, ...]:
    accepted: list[DurableMemoryEntry] = []
    for entry in entries:
        if not isinstance(entry, DurableMemoryEntry):
            raise TypeError("memory_entries must contain DurableMemoryEntry values")
        if entry.section not in _MEMORY_SECTION_TITLES:
            raise ValueError(f"unsupported Memory section: {entry.section!r}")
        score = confidence(entry.confidence)
        text = _validated_entry_text(entry.text)
        if score >= min_confidence and text and not _is_transient(text):
            accepted.append(DurableMemoryEntry(entry.section, text, score))
    return tuple(accepted)


def _validated_tool_entries(
    entries: Iterable[ToolExperienceEntry],
    *,
    allowed_tools: frozenset[str],
    task_notes: TaskNotes,
    min_confidence: float,
) -> tuple[ToolExperienceEntry, ...]:
    accepted: list[ToolExperienceEntry] = []
    for entry in entries:
        if not isinstance(entry, ToolExperienceEntry):
            raise TypeError(
                "tool_experience_entries must contain ToolExperienceEntry values"
            )
        if entry.tool not in allowed_tools:
            raise ValueError(
                f"Tool Experience references unregistered tool {entry.tool!r}."
            )
        if entry.outcome not in _TOOL_SECTION_TITLES:
            raise ValueError(f"unsupported Tool Experience outcome: {entry.outcome!r}")
        if not _SAFE_TOOL_NAME.fullmatch(entry.tool):
            raise ValueError(f"unsafe tool name for experience file: {entry.tool!r}")
        score = confidence(entry.confidence)
        text = _validated_entry_text(entry.entry)
        supported = (
            task_notes.has_failure_followed_by_confirmed_recovery(entry.tool)
            if entry.outcome == "failure"
            else task_notes.has_confirmed_success(entry.tool)
        )
        if score >= min_confidence and text and not _is_transient(text) and supported:
            accepted.append(
                ToolExperienceEntry(
                    entry.tool,
                    entry.outcome,
                    text,
                    score,
                )
            )
    return tuple(accepted)


def _validated_confidence_threshold(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("min_confidence must be a number between 0 and 1")
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_confidence must be a number between 0 and 1") from exc
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("min_confidence must be a number between 0 and 1")
    return threshold


def _is_transient(text: str) -> bool:
    return any(pattern.search(text) for pattern in _TRANSIENT_PATTERNS)


def _validated_entry_text(value: str) -> str:
    text = " ".join(str(value or "").split()).lstrip("- ").strip()
    if len(text) > _MAX_ENTRY_CHARS:
        raise ValueError(
            f"Memory entry exceeds {_MAX_ENTRY_CHARS} characters after normalization."
        )
    return text


def _parse_markdown_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            sections.setdefault(current, [])
        elif current and stripped.startswith("- "):
            value = stripped[2:].strip()
            if value:
                sections[current].append(value)
    return sections


def _parse_memory_sections(text: str) -> dict[str, list[str]]:
    """Parse sectioned Memory while accepting tagged or legacy bullets."""
    sections = _parse_markdown_sections(text)
    for title, tag in _MEMORY_TAGS_BY_TITLE.items():
        prefix = f"[{tag}] "
        sections[title] = [
            entry[len(prefix) :] if entry.startswith(prefix) else entry
            for entry in sections.get(title, [])
        ]
    return sections


def _render_memory_store(sections: Mapping[str, list[str]]) -> str:
    """Render controlled sections with the Listing's visible entry labels."""
    tagged: dict[str, list[str]] = {}
    for title in _MEMORY_SECTION_TITLES.values():
        tag = _MEMORY_TAGS_BY_TITLE[title]
        tagged[title] = [f"[{tag}] {entry}" for entry in sections.get(title, [])]
    return _render_markdown_store(
        "Memory",
        tagged,
        tuple(_MEMORY_SECTION_TITLES.values()),
    )


def _render_markdown_store(
    title: str,
    sections: Mapping[str, list[str]],
    section_order: tuple[str, ...],
) -> str:
    lines = [f"# {title}"]
    for section in section_order:
        lines.extend(["", f"## {section}"])
        lines.extend(f"- {entry}" for entry in sections.get(section, []))
    return "\n".join(lines).rstrip() + "\n"


def _append_unique(entries: list[str], value: str) -> bool:
    normalized = value.casefold()
    if any(existing.casefold() == normalized for existing in entries):
        return False
    entries.append(value)
    return True


__all__ = [
    "DurableMemoryEntry",
    "FileMemory",
    "MemoryConsolidation",
    "MemoryConsolidatorProtocol",
    "MemorySection",
    "ModelMemoryConsolidator",
    "ModelToolExperienceSummarizer",
    "TaskNotes",
    "TaskNotesSnapshot",
    "ToolExperienceEntry",
    "ToolExperienceOutcome",
    "ToolExperienceSummarizerProtocol",
]
