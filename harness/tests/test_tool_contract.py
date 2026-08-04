from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from harness.tools.registry import BuiltinTool, ToolRegistry


class _DeclaredTool:
    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    @property
    def name(self) -> str:
        return "inspect"

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    def call(self, args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {"success": True, "observation": "ready"}


@dataclass(frozen=True)
class _FieldDescription:
    description: str


def test_tool_definition_contains_exactly_the_three_paper_fields() -> None:
    registry = ToolRegistry()
    registry.register(
        _DeclaredTool(
            {
                "name": "inspect",
                "description": "Inspect current evidence.",
                "inputSchema": {"type": "object"},
            }
        )
    )

    assert set(registry.list_tool_definitions()[0]) == {
        "name",
        "description",
        "inputSchema",
    }

    with pytest.raises(ValueError, match="outside the Tool Definition"):
        ToolRegistry().register(
            _DeclaredTool(
                {
                    "name": "inspect",
                    "description": "Inspect current evidence.",
                    "inputSchema": {"type": "object"},
                    "postCondition": "Hidden from the model.",
                }
            )
        )


def test_tool_definition_requires_a_model_usable_description() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        ToolRegistry().register(
            _DeclaredTool(
                {
                    "name": "inspect",
                    "description": " ",
                    "inputSchema": {"type": "object"},
                }
            )
        )


def test_tool_registration_rejects_ambiguous_required_declarations() -> None:
    with pytest.raises(ValueError, match="duplicate property names"):
        ToolRegistry().register(
            _DeclaredTool(
                {
                    "name": "inspect",
                    "description": "Inspect one named view.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"view": {"type": "string"}},
                        "required": ["view", "view"],
                    },
                }
            )
        )


def test_tool_call_validates_arguments_before_execution() -> None:
    executed = False

    def inspect(view: str) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"success": True, "observation": view}

    registry = ToolRegistry()
    registry.register(
        BuiltinTool(
            "inspect",
            "Inspect one named view.",
            {
                "type": "object",
                "properties": {"view": {"type": "string", "minLength": 1}},
                "required": ["view"],
                "additionalProperties": False,
            },
            inspect,
        )
    )

    result = registry.call_tool("inspect", {"view": "", "unexpected": True})

    assert result["success"] is False
    assert result["kind"] == "invalid_tool_arguments"
    assert executed is False


def test_callable_registration_reads_listing_contract_metadata() -> None:
    registry = ToolRegistry()

    def pick_object(
        obj: Annotated[
            str,
            _FieldDescription("The confirmed object ref to grasp."),
        ],
    ) -> dict[str, Any]:
        """Grasp one confirmed object."""
        return {"success": True, "observation": f"grasped {obj}"}

    pick_object.POST_CONDITION = "The requested object is secured."  # type: ignore[attr-defined]
    registry.tool()(pick_object)

    definition = registry.list_tool_definitions()[0]
    assert definition["name"] == "pick_object"
    assert definition["inputSchema"]["properties"]["obj"]["description"] == (
        "The confirmed object ref to grasp."
    )
    assert registry.post_condition("pick_object") == (
        "The requested object is secured."
    )


@pytest.mark.parametrize(
    ("raw", "reason_fragment"),
    [
        ({"observation": "missing success"}, "missing required boolean"),
        ({"success": "yes", "observation": "bad flag"}, "must be a boolean"),
        ({"success": True}, "at least one value field"),
        ({"success": False}, "non-empty string field 'reason'"),
    ],
)
def test_tool_result_envelope_fails_closed(
    raw: dict[str, Any],
    reason_fragment: str,
) -> None:
    registry = ToolRegistry()
    registry.register(
        BuiltinTool(
            "inspect",
            "Inspect current evidence.",
            {"type": "object", "additionalProperties": False},
            lambda: raw,
        )
    )

    result = registry.call_tool("inspect", {})

    assert result["success"] is False
    assert result["kind"] == "tool_protocol_violation"
    assert reason_fragment in result["reason"]
