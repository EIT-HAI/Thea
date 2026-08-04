# Scene Graph

The Scene Graph makes a physical environment readable and addressable across
Turns. Current images and depth describe one view at one moment. The working
graph organizes that evidence into persistent object refs, coarse spatial
state, robot state, typed relations, and freshness metadata.

The Harness does not prescribe a detector, mapper, database, or robot SDK.
Those systems remain behind `SceneGraphProtocol`.

## What the public release provides

| Thea provides | A deployment provides |
|---|---|
| `SceneGraphProtocol` for refresh, Brief generation, and confirmed execution updates | Perception, localization, tracking, association, and graph storage |
| `SceneGraphBrief` and typed decision-facing records | Conversion from the working graph to those records |
| Canonical Brief rendering into Refreshed context | The coordinate frame and provenance carried by the Brief |
| `SceneGraphQueryProtocol` | Relation and stored-image lookup by existing ref |
| `get_object_relations` and `get_image` registration | The underlying relation edges and visual evidence |
| Evaluator-gated execution update call | The deployment-specific state changes caused by a successful Tool |

The public package therefore reproduces the Harness-side contract, not a
complete scene-understanding stack.

## Working graph and Scene Graph Brief

The deployment-owned working graph may contain detailed geometry, all relation
edges, stored visual evidence, and provider-specific state. Passing that graph
to the model every Turn would repeatedly consume context capacity with details
that most decisions do not need.

`SceneGraphBrief` is the field-selected projection placed in Refreshed
context. It retains every object ref while selecting only frequently needed
fields:

```python
from harness import (
    SceneGraphBrief,
    SceneGraphObjectBrief,
    SceneGraphRobotBrief,
)

brief = SceneGraphBrief(
    objects=(
        SceneGraphObjectBrief(
            ref="cup_2",
            coarse_position=(3.91, -0.18, 1.08),
            confidence=1.0,
            freshness="current",
        ),
    ),
    robot=SceneGraphRobotBrief(
        pose=(0.0, 0.0, 0.0),
        holding=(),
    ),
    freshness="current",
    provenance="rgbd_scene_graph",
    coordinate_frame="map",
    updated_at="2026-07-27T12:00:00Z",
)
```

Object records can additionally carry `container_state` and tracked
`contents`. The working graph may encode `on`, `inside`, `holding`, and `near`
relations. Relations and stored images remain behind ref-keyed queries rather
than entering the Brief by default.

Compactness comes from field selection, not node pruning.

## Connect an existing Scene Graph

Implement the persistent world-state boundary:

```python
from harness import SceneGraphBrief


class MySceneGraph:
    def refresh_from_perception(self) -> None:
        merge_latest_perception_into_working_graph()

    def brief(self) -> SceneGraphBrief:
        return project_working_graph_to_brief()

    def apply_confirmed_execution(
        self,
        *,
        tool_name,
        arguments,
        result,
        evaluator_verdict,
    ):
        return apply_execution_update(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            verdict=evaluator_verdict,
        )
```

Pass the provider to `Harness`:

```python
harness = Harness(
    config,
    scene_graph=scene_graph,
    observation_provider=observation_provider,
    registry=registry,
)
```

Before each model call, the Harness invokes `refresh_from_perception()`, reads
`brief()`, and regenerates the text placed in Refreshed context. The working
graph itself persists across calls.

## Add ref-keyed queries

Use the same provider to expose omitted detail:

```python
from harness import (
    SceneGraphRelation,
    VisualEvidence,
    register_scene_graph_query_tools,
)


class MySceneGraph:
    # SceneGraphProtocol methods omitted.

    def has_ref(self, ref):
        return ref in self.objects

    def get_object_relations(self, ref, relation=None):
        return (
            SceneGraphRelation(
                relation="near",
                other_ref="desk_12",
                distance_m=0.4,
            ),
        )

    def get_image(self, ref):
        return (
            VisualEvidence(
                name="stored-front",
                data=read_stored_image(ref),
                media_type="image/jpeg",
                freshness="last_seen",
            ),
        )


register_scene_graph_query_tools(registry, scene_graph)
```

The registered Tools accept `obj` as the model-visible parameter. Its value is
an existing Scene Graph ref:

- `get_object_relations(obj, relation=None)`
- `get_image(obj)`

Both require a ref already present in the latest Brief. Stored visual evidence
may be historical; current manipulation evidence belongs in a fresh
Observation.

## Reproduce a compatible backend

A compatible backend needs the following pipeline:

1. **Acquire evidence.** Read the deployment's RGB, depth, pose, and other
   localization inputs.
2. **Create object observations.** Detect or segment entities and estimate
   coarse geometry in a declared coordinate frame.
3. **Resolve refs.** Associate compatible observations across frames so the
   same entity retains one ref whenever possible.
4. **Maintain graph state.** Update object and container nodes, robot pose and
   holding state, freshness, provenance, timestamps, and the supported typed
   relations.
5. **Retain visual evidence.** Keep a ref-to-image index that can return
   `VisualEvidence` without exposing deployment-local file paths.
6. **Project the Brief.** Return all working-graph refs with selected coarse
   fields through `SceneGraphBrief`.
7. **Apply confirmed action updates.** Change execution-derived state only
   after the Harness passes an evaluator-confirmed success to
   `apply_confirmed_execution()`.

[SysNav](https://arxiv.org/abs/2603.06914) is one useful systems reference for
constructing a structured scene representation from real-world perception for
cross-embodiment ObjectNav. It can inform the perception, mapping, and
hierarchical spatial-representation side of a backend. Thea adds a different
boundary around that representation: a field-selected Brief for model context,
queries keyed by persistent ref, and evaluator-gated execution updates.

SysNav is a reference design, not a dependency. Any backend that satisfies the
public protocols can be used.

## Verify the integration

Check the boundary in this order:

1. `brief()` returns unique refs and valid confidence values.
2. `render_scene_graph_brief(brief)` lists every ref expected by the model.
3. `get_object_relations` and `get_image` reject unknown refs.
4. The Brief is regenerated after perception refresh.
5. A failed evaluator verdict leaves execution-derived graph state unchanged.
6. A successful verdict applies exactly one deployment-defined update.

The Scene Graph identifies an entity and its coarse location. It does not
decide whether the current pose is ready for manipulation or whether an action
succeeded.
