from __future__ import annotations

import pytest
from harness.context import ToolCall
from harness.context.rendering import build_refreshed_context
from harness.tools.evidence import transient_tool_result_evidence_message
from harness.tools.registry import ToolRegistry
from harness.world.observation import VisualEvidence
from harness.world.scene_graph import (
    SceneGraphBrief,
    SceneGraphObjectBrief,
    SceneGraphQueryProtocol,
    SceneGraphRelation,
    SceneGraphRobotBrief,
    register_scene_graph_query_tools,
    render_scene_graph_brief,
)


class ExampleSceneGraphQueries:
    def __init__(self) -> None:
        self.refs = {"cup_2", "desk_12"}
        self.relation_calls: list[tuple[str, str | None]] = []
        self.image_calls: list[str] = []

    def has_ref(self, ref: str) -> bool:
        return ref in self.refs

    def get_object_relations(
        self,
        ref: str,
        relation: str | None = None,
    ) -> tuple[SceneGraphRelation, ...]:
        self.relation_calls.append((ref, relation))
        return (
            SceneGraphRelation(
                relation="near",
                other_ref="desk_12",
                distance_m=0.4,
            ),
        )

    def get_image(self, ref: str) -> tuple[VisualEvidence, ...]:
        self.image_calls.append(ref)
        return (
            VisualEvidence(
                name="stored-view",
                url="https://example.test/cup_2.png",
                caption="Stored evidence for cup_2",
                freshness="last_seen",
            ),
        )


def test_scene_graph_brief_renders_all_refs_and_paper_fields() -> None:
    brief = SceneGraphBrief(
        objects=(
            SceneGraphObjectBrief(
                ref="cup_2",
                coarse_position=(3.91, -0.18, 1.08),
                confidence=1.0,
                freshness="current",
            ),
            SceneGraphObjectBrief(
                ref="drawer_7",
                coarse_position=(2.0, 1.0, 0.6),
                confidence=0.9,
                freshness="last_seen",
                container_state="open",
                contents=("battery_4",),
            ),
        ),
        robot=SceneGraphRobotBrief(
            pose=(0.0, 0.0, 0.0),
            holding=("cup_2",),
        ),
        freshness="current",
        provenance="example_provider",
        coordinate_frame="map",
        updated_at=1778162862.957,
    )

    rendered = render_scene_graph_brief(brief)

    assert rendered.startswith("Scene graph brief:\n  objects=2;")
    assert "stamp=1778162862.957" in rendered
    assert "provenance=example_provider" in rendered
    assert "coordinate_frame=map" in rendered
    assert "Robot: pose=[0, 0, 0],\n  gripper holding [cup_2]" in rendered
    assert "- cup_2 at (3.91, -0.18, 1.08);" in rendered
    assert "conf=1\n    freshness=current" in rendered
    assert "- drawer_7 at (2, 1, 0.6);" in rendered
    assert "container_state=open\n    contents=[battery_4]" in rendered
    assert "relations" not in rendered.lower()
    assert rendered.count("\n  - ") == 2

    refreshed = build_refreshed_context(scene_graph_brief=rendered)
    assert refreshed.startswith(
        "## Scene Graph Brief\nScene graph brief:\n  objects=2;"
    )
    assert refreshed.count("## Scene Graph Brief") == 1


@pytest.mark.parametrize(
    ("factory", "error"),
    [
        (
            lambda: SceneGraphObjectBrief(ref="cup_2", freshness={"age": 1}),
            TypeError,
        ),
        (
            lambda: SceneGraphBrief(updated_at=float("inf")),
            ValueError,
        ),
    ],
)
def test_scene_graph_metadata_rejects_non_scalar_or_nonfinite_values(
    factory,
    error,
) -> None:
    with pytest.raises(error):
        factory()


def test_scene_graph_renderer_requires_the_structured_public_brief() -> None:
    with pytest.raises(TypeError, match="SceneGraphBrief"):
        render_scene_graph_brief("objects=1")  # type: ignore[arg-type]


def test_query_tool_registration_has_exactly_the_two_ref_keyed_tools() -> None:
    registry = ToolRegistry()
    provider = ExampleSceneGraphQueries()

    register_scene_graph_query_tools(registry, provider)

    assert isinstance(provider, SceneGraphQueryProtocol)
    assert registry.names() == {"get_object_relations", "get_image"}
    definitions = {item["name"]: item for item in registry.list_tool_definitions()}
    assert definitions["get_object_relations"]["inputSchema"]["required"] == ["obj"]
    assert set(definitions["get_object_relations"]["inputSchema"]["properties"]) == {
        "obj",
        "relation",
    }
    assert definitions["get_object_relations"]["inputSchema"]["properties"]["relation"][
        "enum"
    ] == ["on", "inside", "holding", "near"]
    assert definitions["get_image"]["inputSchema"]["required"] == ["obj"]
    assert set(definitions["get_image"]["inputSchema"]["properties"]) == {"obj"}
    assert "find_object" not in registry.names()


def test_get_object_relations_returns_typed_results_for_an_existing_ref() -> None:
    registry = ToolRegistry()
    provider = ExampleSceneGraphQueries()
    register_scene_graph_query_tools(registry, provider)

    result = registry.call_tool(
        "get_object_relations",
        {"obj": "cup_2", "relation": "near"},
    )

    assert result == {
        "success": True,
        "observation": "Retrieved 1 relation for cup_2.",
        "ref": "cup_2",
        "relation_filter": "near",
        "relations": [
            {
                "relation": "near",
                "other_ref": "desk_12",
                "distance_m": 0.4,
            }
        ],
        "provenance": "scene_graph",
    }
    assert provider.relation_calls == [("cup_2", "near")]


def test_queries_reject_unknown_refs_before_reading_provider_state() -> None:
    registry = ToolRegistry()
    provider = ExampleSceneGraphQueries()
    register_scene_graph_query_tools(registry, provider)

    result = registry.call_tool("get_image", {"obj": "cup_99"})

    assert result["success"] is False
    assert result["kind"] == "scene_graph_ref_not_found"
    assert result["ref"] == "cup_99"
    assert provider.image_calls == []


def test_get_image_returns_lark_and_model_compatible_visual_outputs() -> None:
    registry = ToolRegistry()
    provider = ExampleSceneGraphQueries()
    register_scene_graph_query_tools(registry, provider)

    result = registry.call_tool("get_image", {"obj": "cup_2"})

    assert result["success"] is True
    assert result["provenance"] == "scene_graph"
    assert result["visual_outputs"] == [
        {
            "type": "image_url",
            "image_url": {"url": "https://example.test/cup_2.png"},
            "caption": "Stored evidence for cup_2",
            "view": "stored-view",
            "label": "stored-view",
            "freshness": "last_seen",
        }
    ]
    transient = transient_tool_result_evidence_message(
        ToolCall(id="call-1", name="get_image", arguments={"obj": "cup_2"}),
        result,
    )
    assert transient is not None
    assert transient["content"][1] == result["visual_outputs"][0]
