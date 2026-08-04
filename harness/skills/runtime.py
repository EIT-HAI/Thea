"""Task-scoped state boundary for the progressively disclosed Skill System."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness.context import ToolCall
from harness.runtime.execution import ToolExecution
from harness.skills.discovery import (
    Skill,
    skill_body_by_name,
    skill_resource_by_name,
)
from harness.tools.registry import BuiltinTool, ToolRegistry


@dataclass(frozen=True)
class LoadedSkillContext:
    """One loaded skill rendered into task-resident model context."""

    blocks: tuple[tuple[str, str], ...]
    metadata: tuple[dict[str, Any], ...]


def _normalize_loaded_skill(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate and copy a skill body before it enters task-resident context."""
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    instructions = str(value.get("instructions") or "").strip()
    if not name or not instructions:
        return None
    raw_resources = value.get("resources")
    resources = (
        [str(item).strip() for item in raw_resources if str(item).strip()]
        if isinstance(raw_resources, (list, tuple))
        else []
    )
    return {
        "name": name,
        "description": str(value.get("description") or "").strip(),
        "instructions": instructions,
        "resources": resources,
    }


def render_loaded_skill_context(
    loaded_skill: dict[str, Any] | None,
) -> LoadedSkillContext:
    """Render one loaded body without mixing it into accumulated messages."""
    normalized = _normalize_loaded_skill(loaded_skill)
    if normalized is None:
        return LoadedSkillContext((), ())

    name = normalized["name"]
    lines = [
        f"[Loaded task skill: {name}]",
        "Apply these instructions to the current task only.",
    ]
    if normalized["description"]:
        lines.extend(["", normalized["description"]])
    lines.extend(["", normalized["instructions"]])
    resources = normalized["resources"]
    if resources:
        lines.extend(
            [
                "",
                "Bundled resources:",
                *(f"- {resource}" for resource in resources),
            ]
        )
    content = "\n".join(lines)
    return LoadedSkillContext(
        blocks=(("Loaded Skill", content),),
        metadata=(
            {
                "name": "Loaded Skill",
                "kind": "resident_context",
                "source": f"skill:{name}",
                "scope": "task",
                "char_count": len(content),
            },
        ),
    )


class SkillSystem:
    """Own Skill tools and the one skill body loaded for the active task."""

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = tuple(skills)
        self._loaded_skill: dict[str, Any] | None = None

    @property
    def loaded_skill(self) -> dict[str, Any] | None:
        """Return a copy of the body currently loaded into task context."""
        skill = self._loaded_skill
        if skill is None:
            return None
        return {
            **skill,
            "resources": list(skill["resources"]),
        }

    def begin_task(
        self,
        loaded_skill: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Start a task with at most one explicitly supplied skill body."""
        self._loaded_skill = _normalize_loaded_skill(loaded_skill)
        return self.loaded_skill

    def end_task(self) -> None:
        """Expire the loaded body at the task boundary."""
        self._loaded_skill = None

    def context(self) -> LoadedSkillContext:
        """Render the loaded body without entering accumulated messages."""
        return render_loaded_skill_context(self._loaded_skill)

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register Skill System tools in the same flat registry as all tools."""
        if not self._skills:
            return
        if not registry.has("load_skill"):
            registry.register(
                BuiltinTool(
                    name="load_skill",
                    description=(
                        "Load one registered skill when its catalog description "
                        "matches the current task. The result lists bundled "
                        "resources; load one only when the instructions require it."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": [skill.name for skill in self._skills],
                                "description": (
                                    "Exact skill name from the Skill Catalog."
                                ),
                            },
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                    fn=self._load_skill,
                )
            )

        resource_skill_names = [skill.name for skill in self._skills if skill.resources]
        if not resource_skill_names or registry.has("load_skill_resource"):
            return
        registry.register(
            BuiltinTool(
                name="load_skill_resource",
                description=(
                    "Load one declared resource from the skill loaded for the "
                    "active task. Use the exact path returned by load_skill."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": resource_skill_names,
                            "description": "Exact loaded skill name.",
                        },
                        "resource": {
                            "type": "string",
                            "description": (
                                "Exact relative resource path returned by load_skill."
                            ),
                        },
                    },
                    "required": ["name", "resource"],
                    "additionalProperties": False,
                },
                fn=self._load_skill_resource,
            )
        )

    def apply_tool_result(
        self,
        call: ToolCall,
        execution: ToolExecution,
    ) -> ToolExecution:
        """Move a disclosed skill body from Tool Result into task context."""
        result = execution.result
        if call.name != "load_skill" or not bool(result.get("success")):
            return execution

        loaded_skill = _normalize_loaded_skill(result)
        requested_name = str(call.arguments.get("name") or "").strip()
        if loaded_skill is None or loaded_skill["name"] != requested_name:
            return execution

        self._loaded_skill = loaded_skill
        confirmation = {
            "success": True,
            "loaded": True,
            "name": loaded_skill["name"],
            "description": loaded_skill["description"],
            "resources": list(loaded_skill["resources"]),
            "scope": "task",
        }
        events = tuple(
            (
                {
                    **event,
                    "result": confirmation,
                    "success": True,
                }
                if event.get("type") == "tool_result" and event.get("id") == call.id
                else event
            )
            for event in execution.events
        )
        return ToolExecution(
            result=confirmation,
            events=(
                *events,
                skill_loaded_event(loaded_skill, source="load_skill"),
            ),
            effective_call=execution.effective_call,
            cancelled=execution.cancelled,
        )

    def _load_skill(self, name: str) -> dict[str, Any]:
        requested = str(name or "").strip()
        loaded = self._loaded_skill
        if loaded is not None and loaded["name"] == requested:
            return {
                "success": True,
                "executed": False,
                "kind": "skill_already_loaded",
                "name": requested,
                "reason": "Skill is already loaded for this task.",
            }
        return skill_body_by_name(list(self._skills), requested)

    def _load_skill_resource(
        self,
        name: str,
        resource: str,
    ) -> dict[str, Any]:
        loaded = self._loaded_skill
        if loaded is None:
            return {
                "success": False,
                "reason": "No skill is loaded for the active task.",
                "kind": "skill_not_loaded",
            }

        requested_name = str(name or "").strip()
        if requested_name != loaded["name"]:
            return {
                "success": False,
                "reason": (
                    f"Skill {requested_name!r} is not loaded for the active task."
                ),
                "kind": "skill_not_loaded",
                "loaded_skill": loaded["name"],
            }

        requested_resource = str(resource or "").strip().replace("\\", "/")
        if requested_resource not in loaded["resources"]:
            return {
                "success": False,
                "reason": (
                    f"Resource {requested_resource!r} is not declared by "
                    f"loaded skill {requested_name!r}."
                ),
                "kind": "skill_resource_not_declared",
                "available_resources": list(loaded["resources"]),
            }
        return skill_resource_by_name(
            list(self._skills),
            requested_name,
            requested_resource,
        )


def skill_loaded_event(
    skill: dict[str, Any],
    *,
    source: str = "",
) -> dict[str, Any]:
    """Build the stable observability event for one task-scoped skill."""
    event: dict[str, Any] = {
        "type": "skill_loaded",
        "name": str(skill.get("name") or ""),
        "description": str(skill.get("description") or ""),
        "scope": "task",
    }
    if source:
        event["source"] = source
        event["resources"] = list(skill.get("resources") or [])
    return event


__all__ = [
    "LoadedSkillContext",
    "SkillSystem",
    "render_loaded_skill_context",
    "skill_loaded_event",
]
