"""Task-local working record used by the Agentic Loop."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import TaskNotesSnapshot


@dataclass
class TaskNotes:
    """Maintain one replaceable goal, phase, and Tool-result timeline."""

    mirror_path: Path | None = None
    max_events: int = 40
    goal: str = ""
    phase: str = ""
    timeline: list[dict[str, Any]] = field(default_factory=list)
    _revision: int = 0
    _rendered_revision: int = -1

    def reset(self, goal: str) -> None:
        self.goal = str(goal or "").strip()
        self.phase = "task started"
        self.timeline = [{"event": "task_start", "goal": self.goal}]
        self._revision += 1
        self._rendered_revision = -1
        self._mirror()

    def revise_goal(self, goal: str) -> None:
        self.goal = str(goal or "").strip()
        self.phase = "instruction revised"
        self.timeline.append({"event": "instruction_revised", "goal": self.goal})
        self._trim()
        self._revision += 1
        self._mirror()

    def append_tool_result(
        self,
        *,
        tool: str,
        result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
        evaluator_verdict: dict[str, Any] | None = None,
    ) -> None:
        verdict = (
            dict(evaluator_verdict) if isinstance(evaluator_verdict, Mapping) else {}
        )
        status = str(verdict.get("status") or "").strip().lower()
        event = {
            "event": "tool_result",
            "tool": str(tool or "").strip(),
            "arguments": _bounded_mapping(arguments),
            "success": bool(result.get("success")),
            "result": _compact_result(result),
            "evaluator_verdict": _compact_verdict(verdict),
        }
        self.timeline.append(event)
        self._trim()
        outcome = status or ("success" if event["success"] else "failure")
        self.phase = f"{event['tool']}: {outcome}"
        self._revision += 1
        self._mirror()

    def snapshot_message_if_changed(self) -> dict[str, Any] | None:
        """Return the current snapshot only when its content changed."""
        if not self.goal or self._rendered_revision == self._revision:
            return None
        self._rendered_revision = self._revision
        return {"role": "user", "content": self.render()}

    def snapshot(self) -> TaskNotesSnapshot:
        return TaskNotesSnapshot(
            goal=self.goal,
            phase=self.phase,
            timeline=tuple(dict(event) for event in self.timeline),
        )

    def render(self) -> str:
        lines = [
            "Task Notes snapshot:",
            "Task summary:",
            f"- Goal: {self.goal}",
            f"- Current phase: {self.phase}",
            "Timeline:",
        ]
        lines.extend(f"- {_render_timeline_event(event)}" for event in self.timeline)
        return "\n".join(lines)

    def expire(self) -> None:
        self.goal = ""
        self.phase = ""
        self.timeline = []
        self._revision += 1
        self._rendered_revision = -1
        self._mirror()

    def has_confirmed_success(self, tool: str) -> bool:
        """Return whether the trajectory contains evaluator-confirmed success."""
        name = str(tool or "").strip()
        return any(
            str(event.get("tool") or "") == name
            and str((event.get("evaluator_verdict") or {}).get("status") or "").strip()
            == "success"
            for event in self.timeline
            if isinstance(event.get("evaluator_verdict"), Mapping)
        )

    def has_failure_followed_by_confirmed_recovery(self, tool: str) -> bool:
        """Return whether a tool failure is followed by success of that tool."""
        name = str(tool or "").strip()
        saw_failure = False
        for event in self.timeline:
            verdict = event.get("evaluator_verdict")
            if not isinstance(verdict, Mapping):
                continue
            status = str(verdict.get("status") or "").strip()
            event_tool = str(event.get("tool") or "")
            if event_tool == name and status == "failure":
                saw_failure = True
            elif saw_failure and event_tool == name and status == "success":
                return True
        return False

    def _trim(self) -> None:
        self.timeline = self.timeline[-max(1, int(self.max_events)) :]

    def _mirror(self) -> None:
        if self.mirror_path is None:
            return
        content = self.render() + "\n" if self.goal else ""
        atomic_write(self.mirror_path, content)


def atomic_write(path: Path, content: str) -> None:
    """Replace a UTF-8 text file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _compact_result(result: Mapping[str, Any]) -> dict[str, Any]:
    retained: dict[str, Any] = {"success": bool(result.get("success"))}
    for key in ("reason", "kind", "status", "state", "run_id"):
        value = result.get(key)
        if value not in (None, ""):
            retained[key] = _bounded_value(value)
    return retained


def _compact_verdict(verdict: Mapping[str, Any]) -> dict[str, Any]:
    retained: dict[str, Any] = {}
    for key in ("status", "failure_reason", "evidence"):
        value = verdict.get(key)
        if value not in (None, "", []):
            retained[key] = _bounded_value(value)
    return retained


def _bounded_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _bounded_value(item) for key, item in list(value.items())[:20]}


def _bounded_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_bounded_value(item) for item in value[:20]]
    if isinstance(value, Mapping):
        return _bounded_mapping(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)[:500]


def _render_timeline_event(event: Mapping[str, Any]) -> str:
    kind = str(event.get("event") or "event")
    if kind == "task_start":
        return "task_start"
    if kind == "instruction_revised":
        return f"instruction_revised: {event.get('goal', '')}"
    tool = str(event.get("tool") or "")
    result = event.get("result")
    success = bool(result.get("success")) if isinstance(result, Mapping) else False
    verdict = event.get("evaluator_verdict")
    status = str(verdict.get("status") or "") if isinstance(verdict, Mapping) else ""
    outcome = status or ("success" if success else "failure")
    details: list[str] = []
    if isinstance(result, Mapping):
        reason = str(result.get("reason") or "").strip()
        if reason:
            details.append(reason[:180])
    if isinstance(verdict, Mapping):
        reason = str(verdict.get("failure_reason") or "").strip()
        if reason and reason not in details:
            details.append(reason[:180])
    suffix = f": {'; '.join(details)}" if details else ""
    return f"{tool}: {outcome}{suffix}"


__all__ = ["TaskNotes"]
