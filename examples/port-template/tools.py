"""Model-visible tool registration for a new robot deployment."""

from __future__ import annotations

from typing import Any, Protocol

from harness import BuiltinTool, ToolRegistry


class RobotClient(Protocol):
    """Replace with the narrow adapter implemented over your robot SDK."""

    def navigate_to(self, target: str) -> dict[str, Any]: ...

    def pick_object(self, obj: str) -> dict[str, Any]: ...

    def place_object(self, obj: str, target_ref: str) -> dict[str, Any]: ...


def register_robot_tools(registry: ToolRegistry, robot: RobotClient) -> None:
    """Register deployment tools without importing the SDK into the Harness."""
    registry.register(
        BuiltinTool(
            name="navigate_to",
            description="Navigate to one confirmed Scene Graph ref.",
            input_schema=_one_ref_schema("target"),
            fn=robot.navigate_to,
        )
    )
    registry.register(
        BuiltinTool(
            name="pick_object",
            description="Pick one confirmed object ref using current evidence.",
            input_schema=_one_ref_schema("obj"),
            fn=robot.pick_object,
            post_condition="The requested object is secured by the robot.",
        )
    )
    registry.register(
        BuiltinTool(
            name="place_object",
            description="Place the held object on one confirmed target ref.",
            input_schema={
                "type": "object",
                "properties": {
                    "obj": {"type": "string", "minLength": 1},
                    "target_ref": {"type": "string", "minLength": 1},
                },
                "required": ["obj", "target_ref"],
                "additionalProperties": False,
            },
            fn=robot.place_object,
            post_condition="The requested object rests on the target surface.",
        )
    )


def _one_ref_schema(field_name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {field_name: {"type": "string", "minLength": 1}},
        "required": [field_name],
        "additionalProperties": False,
    }
