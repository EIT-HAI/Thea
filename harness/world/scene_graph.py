"""Public Scene Graph Brief and ref-keyed query boundaries.

The full working graph remains owned by the deployment's perception backend.
This module defines only the compact projection read by the model and the two
paper-defined tools that retrieve omitted evidence by an existing object ref.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from harness.tools.registry import BuiltinTool, ToolRegistry
from harness.world.observation import VisualEvidence

Freshness = str | int | float | None
SCENE_GRAPH_RELATION_TYPES = ("on", "inside", "holding", "near")
_SCENE_GRAPH_RELATION_TYPE_SET = frozenset(SCENE_GRAPH_RELATION_TYPES)


@dataclass(frozen=True, slots=True)
class SceneGraphObjectBrief:
    """Decision-facing fields retained for one Scene Graph object ref."""

    ref: str
    coarse_position: tuple[float, ...] | None = None
    confidence: float | None = None
    freshness: Freshness = None
    container_state: str = ""
    contents: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        ref = _nonempty_text(self.ref, field_name="ref")
        position = _normalize_position(
            self.coarse_position,
            field_name=f"{ref}.coarse_position",
        )
        freshness = _normalize_metadata_value(
            self.freshness,
            field_name=f"{ref}.freshness",
        )
        confidence = self.confidence
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(
                confidence,
                (int, float),
            ):
                raise TypeError(f"{ref}.confidence must be a number")
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError(f"{ref}.confidence must be between 0 and 1")

        container_state = str(self.container_state or "").strip()
        contents = _normalize_refs(
            self.contents,
            field_name=f"{ref}.contents",
        )
        if len(set(contents)) != len(contents):
            raise ValueError(f"{ref}.contents must not contain duplicate refs")

        object.__setattr__(self, "ref", ref)
        object.__setattr__(self, "coarse_position", position)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "freshness", freshness)
        object.__setattr__(self, "container_state", container_state)
        object.__setattr__(self, "contents", contents)


@dataclass(frozen=True, slots=True)
class SceneGraphRobotBrief:
    """Robot pose and holding state exposed in the Scene Graph Brief."""

    pose: tuple[float, ...] | None = None
    holding: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        pose = _normalize_position(self.pose, field_name="robot.pose")
        holding = _normalize_refs(self.holding, field_name="robot.holding")
        if len(set(holding)) != len(holding):
            raise ValueError("robot.holding must not contain duplicate refs")
        object.__setattr__(self, "pose", pose)
        object.__setattr__(self, "holding", holding)


@dataclass(frozen=True, slots=True)
class SceneGraphBrief:
    """Field-selected projection of a provider-owned working Scene Graph.

    ``objects`` is intentionally not bounded or pruned: providers supply every
    object ref in the working graph, while relation edges, extents, stored
    images, and provider-specific fields stay behind the query boundary.
    """

    objects: tuple[SceneGraphObjectBrief, ...] = field(default_factory=tuple)
    robot: SceneGraphRobotBrief | None = None
    freshness: Freshness = None
    provenance: str = ""
    coordinate_frame: str = ""
    updated_at: str | int | float | None = None

    def __post_init__(self) -> None:
        objects = tuple(self.objects)
        if any(not isinstance(item, SceneGraphObjectBrief) for item in objects):
            raise TypeError(
                "SceneGraphBrief.objects must contain SceneGraphObjectBrief records"
            )
        refs = [item.ref for item in objects]
        if len(set(refs)) != len(refs):
            raise ValueError("SceneGraphBrief.objects must contain unique refs")
        if self.robot is not None and not isinstance(
            self.robot,
            SceneGraphRobotBrief,
        ):
            raise TypeError("SceneGraphBrief.robot must be SceneGraphRobotBrief")
        freshness = _normalize_metadata_value(
            self.freshness,
            field_name="graph.freshness",
        )
        updated_at = _normalize_metadata_value(
            self.updated_at,
            field_name="graph.updated_at",
        )
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "freshness", freshness)
        object.__setattr__(self, "provenance", str(self.provenance or "").strip())
        object.__setattr__(
            self,
            "coordinate_frame",
            str(self.coordinate_frame or "").strip(),
        )
        object.__setattr__(self, "updated_at", updated_at)


@dataclass(frozen=True, slots=True)
class SceneGraphRelation:
    """One typed relation connecting the queried ref to another graph ref."""

    relation: str
    other_ref: str
    distance_m: float | None = None

    def __post_init__(self) -> None:
        relation = _nonempty_text(self.relation, field_name="relation")
        if relation not in _SCENE_GRAPH_RELATION_TYPE_SET:
            raise ValueError(
                "relation must be one of " + ", ".join(SCENE_GRAPH_RELATION_TYPES)
            )
        other_ref = _nonempty_text(self.other_ref, field_name="other_ref")
        distance = self.distance_m
        if distance is not None:
            if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                raise TypeError("distance_m must be a number")
            distance = float(distance)
            if not math.isfinite(distance) or distance < 0:
                raise ValueError("distance_m must be finite and non-negative")
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "other_ref", other_ref)
        object.__setattr__(self, "distance_m", distance)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "relation": self.relation,
            "other_ref": self.other_ref,
        }
        if self.distance_m is not None:
            result["distance_m"] = self.distance_m
        return result


@runtime_checkable
class SceneGraphQueryProtocol(Protocol):
    """Read-only query surface over a provider-owned working Scene Graph."""

    def has_ref(self, ref: str) -> bool: ...

    def get_object_relations(
        self,
        ref: str,
        relation: str | None = None,
    ) -> Sequence[SceneGraphRelation]: ...

    def get_image(self, ref: str) -> Sequence[VisualEvidence]: ...


def render_scene_graph_brief(brief: SceneGraphBrief) -> str:
    """Render all decision-facing refs in the paper's compact Brief shape."""
    if not isinstance(brief, SceneGraphBrief):
        raise TypeError("brief must be a SceneGraphBrief")
    lines = [
        "Scene graph brief:",
        f"  objects={len(brief.objects)};",
    ]
    if brief.updated_at not in (None, ""):
        lines.append(f"  stamp={_format_value(brief.updated_at)}")
    metadata = _render_graph_metadata(brief)
    if metadata:
        lines.append(f"  metadata: {metadata}")
    lines.extend(_render_robot_brief(brief.robot))
    lines.append("Observed objects:")
    for item in brief.objects:
        lines.extend(_render_object_brief(item))
    if not brief.objects:
        lines.append("  - none")
    return "\n".join(lines)


def _render_graph_metadata(brief: SceneGraphBrief) -> str:
    metadata: list[str] = []
    if brief.freshness not in (None, ""):
        metadata.append(f"freshness={_format_value(brief.freshness)}")
    if brief.provenance:
        metadata.append(f"provenance={brief.provenance}")
    if brief.coordinate_frame:
        metadata.append(f"coordinate_frame={brief.coordinate_frame}")
    return "; ".join(metadata)


def _render_robot_brief(robot: SceneGraphRobotBrief | None) -> list[str]:
    if robot is None:
        return ["Robot: pose=unavailable,", "  gripper unavailable"]
    pose = (
        "unavailable"
        if robot.pose is None
        else "[" + ", ".join(_format_value(value) for value in robot.pose) + "]"
    )
    gripper = (
        "gripper holding [" + ", ".join(robot.holding) + "]"
        if robot.holding
        else "gripper empty"
    )
    return [f"Robot: pose={pose},", f"  {gripper}"]


def _render_object_brief(item: SceneGraphObjectBrief) -> list[str]:
    location = (
        f" at {_format_position(item.coarse_position)}"
        if item.coarse_position is not None
        else ""
    )
    lines = [f"  - {item.ref}{location};"]
    fields: list[str] = []
    if item.confidence is not None:
        fields.append(f"conf={_format_value(item.confidence)}")
    if item.freshness not in (None, ""):
        fields.append(f"freshness={_format_value(item.freshness)}")
    if item.container_state:
        fields.append(f"container_state={item.container_state}")
    if item.contents:
        fields.append(f"contents=[{', '.join(item.contents)}]")
    lines.extend(f"    {field}" for field in fields)
    return lines


def register_scene_graph_query_tools(
    registry: ToolRegistry,
    provider: SceneGraphQueryProtocol,
) -> None:
    """Register the two paper-defined queries over existing Scene Graph refs."""
    registry.register(
        BuiltinTool(
            name="get_object_relations",
            description=(
                "Return typed relations stored for an existing Scene Graph ref. "
                "Use this when relation detail omitted from the Scene Graph Brief "
                "is needed for the current decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "obj": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "An object ref already present in the Scene Graph Brief."
                        ),
                    },
                    "relation": {
                        "type": "string",
                        "minLength": 1,
                        "enum": list(SCENE_GRAPH_RELATION_TYPES),
                        "description": (
                            "Optional relation type used to filter returned edges."
                        ),
                    },
                },
                "required": ["obj"],
                "additionalProperties": False,
            },
            fn=lambda obj, relation=None: _relations_result(
                provider,
                ref=obj,
                relation=relation,
            ),
        )
    )
    registry.register(
        BuiltinTool(
            name="get_image",
            description=(
                "Return stored visual evidence for an existing Scene Graph ref. "
                "Stored evidence may be historical; use a current Observation "
                "when current local evidence is required."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "obj": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "An object ref already present in the Scene Graph Brief."
                        ),
                    },
                },
                "required": ["obj"],
                "additionalProperties": False,
            },
            fn=lambda obj: _image_result(provider, ref=obj),
        )
    )


def _relations_result(
    provider: SceneGraphQueryProtocol,
    *,
    ref: str,
    relation: str | None,
) -> dict[str, Any]:
    normalized_ref = str(ref or "").strip()
    missing = _missing_ref_result(provider, normalized_ref)
    if missing is not None:
        return missing
    relation_filter = str(relation or "").strip() or None
    relations = tuple(provider.get_object_relations(normalized_ref, relation_filter))
    if any(not isinstance(item, SceneGraphRelation) for item in relations):
        raise TypeError("get_object_relations must return SceneGraphRelation records")
    return {
        "success": True,
        "observation": (
            f"Retrieved {len(relations)} relation"
            f"{'' if len(relations) == 1 else 's'} for {normalized_ref}."
        ),
        "ref": normalized_ref,
        "relation_filter": relation_filter,
        "relations": [item.as_dict() for item in relations],
        "provenance": "scene_graph",
    }


def _image_result(
    provider: SceneGraphQueryProtocol,
    *,
    ref: str,
) -> dict[str, Any]:
    normalized_ref = str(ref or "").strip()
    missing = _missing_ref_result(provider, normalized_ref)
    if missing is not None:
        return missing
    images = tuple(provider.get_image(normalized_ref))
    if any(not isinstance(item, VisualEvidence) for item in images):
        raise TypeError("get_image must return VisualEvidence records")
    return {
        "success": True,
        "observation": (
            f"Retrieved {len(images)} stored image"
            f"{'' if len(images) == 1 else 's'} for {normalized_ref}."
        ),
        "ref": normalized_ref,
        "visual_outputs": [item.visual_output() for item in images],
        "provenance": "scene_graph",
    }


def _missing_ref_result(
    provider: SceneGraphQueryProtocol,
    ref: str,
) -> dict[str, Any] | None:
    if ref and provider.has_ref(ref):
        return None
    label = ref or "<empty>"
    return {
        "success": False,
        "reason": f"No Scene Graph entity exists for ref {label!r}.",
        "kind": "scene_graph_ref_not_found",
        "ref": ref,
    }


def _normalize_position(
    value: Sequence[float] | None,
    *,
    field_name: str,
) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a coordinate sequence")
    normalized: list[float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise TypeError(f"{field_name} coordinates must be numbers")
        number = float(coordinate)
        if not math.isfinite(number):
            raise ValueError(f"{field_name} coordinates must be finite")
        normalized.append(number)
    if len(normalized) not in {2, 3, 4}:
        raise ValueError(f"{field_name} must contain 2, 3, or 4 coordinates")
    return tuple(normalized)


def _normalize_metadata_value(value: Freshness, *, field_name: str) -> Freshness:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be text or a finite number")
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite")
        return value
    raise TypeError(f"{field_name} must be text or a finite number")


def _format_position(position: tuple[float, ...]) -> str:
    return "(" + ", ".join(_format_value(value) for value in position) + ")"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        number = float(value)
        return f"{number:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _nonempty_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_refs(value: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} must be a sequence of refs")
    return tuple(_nonempty_text(item, field_name=field_name) for item in value)


__all__ = [
    "Freshness",
    "SCENE_GRAPH_RELATION_TYPES",
    "SceneGraphBrief",
    "SceneGraphObjectBrief",
    "SceneGraphQueryProtocol",
    "SceneGraphRelation",
    "SceneGraphRobotBrief",
    "register_scene_graph_query_tools",
    "render_scene_graph_brief",
]
