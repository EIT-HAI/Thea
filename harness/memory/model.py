"""LLM-backed Memory consolidation and Tool Experience summarization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from harness.context import Context

from .contracts import (
    DurableMemoryEntry,
    MemoryConsolidation,
    TaskNotesSnapshot,
    ToolExperienceEntry,
)


class ModelMemoryConsolidator:
    """Extract durable Memory and Tool Experience from completed Task Notes."""

    _SYSTEM_PROMPT = """You consolidate completed robot Task Notes into durable memory.
Return one JSON object with exactly two arrays:
- memory_entries: {section, text, confidence}, where section is preferences,
  conventions, or general_lessons and confidence is between 0 and 1;
- tool_experience_entries: {tool, outcome, entry, confidence}, where outcome
  is success or failure, confidence is between 0 and 1, and tool must come
  from allowed_tools.
Keep only durable, reusable facts. Reject transient object refs, coordinates,
distances, current poses, raw observations, and one-run state. A failure
diagnosis becomes a lesson only when the completed trajectory supports it.
Return JSON only."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def consolidate(
        self,
        task_notes: TaskNotesSnapshot,
        *,
        allowed_tools: frozenset[str],
    ) -> MemoryConsolidation:
        payload = {
            "task_notes": {
                "goal": task_notes.goal,
                "phase": task_notes.phase,
                "timeline": list(task_notes.timeline),
            },
            "allowed_tools": sorted(allowed_tools),
        }
        data = _call_json_model(
            self.model,
            system_prompt=self._SYSTEM_PROMPT,
            payload=payload,
        )
        memory_entries = tuple(
            DurableMemoryEntry(
                section=str(item.get("section") or "").strip(),  # type: ignore[arg-type]
                text=str(item.get("text") or ""),
                confidence=confidence(item.get("confidence")),
            )
            for item in _mapping_list(data.get("memory_entries"))
        )
        tool_entries = tuple(
            ToolExperienceEntry(
                tool=str(item.get("tool") or item.get("tool_name") or "").strip(),
                outcome=str(item.get("outcome") or "").strip(),  # type: ignore[arg-type]
                entry=str(item.get("entry") or item.get("text") or ""),
                confidence=confidence(item.get("confidence")),
            )
            for item in _mapping_list(data.get("tool_experience_entries"))
        )
        return MemoryConsolidation(memory_entries, tool_entries)


class ModelToolExperienceSummarizer:
    """Summarize one tool ledger for inclusion in its Tool Definition."""

    _SYSTEM_PROMPT = """Summarize one physical tool's confirmed experience.
Return only one or two concise sentences useful before deciding whether and
how to call this tool. Preserve preconditions and recurring failure modes.
Do not include episode narration, transient object refs, coordinates, or raw
observations."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def summarize(
        self,
        tool_name: str,
        *,
        success: tuple[str, ...],
        failure: tuple[str, ...],
    ) -> str:
        return _call_text_model(
            self.model,
            system_prompt=self._SYSTEM_PROMPT,
            payload={
                "tool_name": tool_name,
                "success": list(success),
                "failure": list(failure),
            },
        )


def confidence(value: Any) -> float:
    """Normalize model-provided confidence to the closed unit interval."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, parsed))


def _call_json_model(
    model: Any,
    *,
    system_prompt: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = _call_text_model(
        model,
        system_prompt=system_prompt,
        payload=payload,
    ).strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Memory consolidation model returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError("Memory consolidation model must return one JSON object.")
    return value


def _call_text_model(
    model: Any,
    *,
    system_prompt: str,
    payload: Mapping[str, Any],
) -> str:
    context = Context()
    context.set_context_layers(resident=system_prompt)
    context.add_user_message(json.dumps(payload, ensure_ascii=False, default=str))
    response = model.call(context, tools=[])
    if list(getattr(response, "tool_calls", []) or []):
        raise ValueError("Memory model returned a Tool Call.")
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise ValueError("Memory model returned an empty response.")
    return text


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise ValueError("Memory consolidation arrays must contain JSON objects.")
    return list(value)


__all__ = [
    "ModelMemoryConsolidator",
    "ModelToolExperienceSummarizer",
]
