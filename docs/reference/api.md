# API

The names below are the complete top-level API exported from `harness` for the
current release. Implement protocol boundaries in deployment code and use the
records and helpers to preserve the shapes consumed by the Agentic Loop.

## Runtime and context

| API | Role |
|---|---|
| `Harness` | Compose one Session and run instruction-driven Tasks |
| `Context` | Hold Resident, Refreshed, and Accumulated context |
| `ModelProtocol` | Provider-neutral model boundary consumed by the loop |
| `ModelResponse` | Normalized model text, reasoning, stop reason, and Tool Calls |
| `ToolCall` | One provider-neutral model-selected Tool request |
| `ToolExecution` | One completed blocking Tool execution and its emitted events |
| `ToolHooks` | Compose deterministic pre-execution and evaluator post-execution behavior |
| `PromptSnapshotBuilder` | Build an observability snapshot of one model request |
| `RuntimeResource` | Closable deployment resource owned by one Harness |

## Configuration and compaction

| API | Role |
|---|---|
| `ConfigurationError` | Invalid trusted runtime configuration |
| `validate_runtime_config()` | Validate Harness-owned configuration sections and invariants |
| `CompactionConfig` | Typed Accumulated-context budgets |
| `ContextCompactor` | Replace an older Accumulated prefix with a bounded checkpoint |

## Tools and MCP

| API | Role |
|---|---|
| `Tool` | Protocol implemented by every executable Tool |
| `BuiltinTool` | In-process Tool with explicit JSON Schema and optional post-condition |
| `MCPTool` | Proxy for a Tool discovered from a trusted MCP process |
| `ToolRegistry` | Register, validate, describe, and execute one flat Tool set |
| `ToolRegistrar` | Callable type for functions that add Tools to a registry |
| `MCPClientProtocol` | Minimal discovery, call, and close boundary for MCP clients |

Provider constructors and adapters live in the explicit `harness.models`
submodule. For example, import `build_model` with
`from harness.models import build_model`.

## Observation and safety

| API | Role |
|---|---|
| `Observation` | Current visual and clearance evidence for one model decision |
| `VisualEvidence` | One named encoded image or HTTP(S)/data URL |
| `BaseClearance` | Forward, backward, left, and right obstacle clearance in metres |
| `ObservationProvider` | Capture current evidence before a model decision |
| `ObservationSink` | Restricted context surface available to an Observation provider |
| `publish_observation()` | Replace the current Observation and emit a bounded public event |
| `render_observation_messages()` | Render typed Observation data for provider adapters |
| `BaseClearanceProvider` | Capture fresh four-direction clearance without exposing a Tool |
| `BaseMotionSafetyFilter` | Refresh clearance and constrain supported base motion |
| `PreExecutionHook` | Protocol for deterministic interception before execution |
| `PreExecutionHookResult` | Rewritten call, hook events, or blocked result returned by a pre-hook |

## Scene Graph

| API | Role |
|---|---|
| `SceneGraphProtocol` | Refresh, Brief, and evaluator-confirmed update boundary for persistent world state |
| `SceneGraphQueryProtocol` | Ref validation, relation lookup, and stored-image lookup boundary |
| `SceneGraphBrief` | Field-selected projection of the working graph |
| `SceneGraphObjectBrief` | Decision-facing fields for one persistent object ref |
| `SceneGraphRobotBrief` | Robot pose and holding state in the Brief |
| `SceneGraphRelation` | One typed relation to another ref |
| `SCENE_GRAPH_RELATION_TYPES` | Supported `on`, `inside`, `holding`, and `near` relation names |
| `render_scene_graph_brief()` | Render the canonical Refreshed-context text |
| `register_scene_graph_query_tools()` | Register `get_object_relations` and `get_image` |
| `NullSceneGraph` | Empty protocol implementation for non-robot or standalone Tasks |

## Evaluation

| API | Role |
|---|---|
| `EvaluationRequest` | Per-Tool post-condition and matching post-execution evidence |
| `EvaluatorProtocol` | Independent outcome-judgment boundary |
| `EvaluatorStatus` | `process`, `success`, or `failure` type alias |
| `EvaluatorVerdict` | Typed status, evidence, and failure cause |
| `PostExecutionObservationProvider` | Resolve a Tool `run_id` to objective evidence |
| `EvaluatorTool` | Hidden adapter that exposes the evaluator through the shared Tool boundary |
| `ModelEvaluator` | Reference multimodal evaluator over any `ModelProtocol` |
| `EvaluatorResponseError` | Malformed model-generated evaluator verdict |

## Memory and Task Notes

| API | Role |
|---|---|
| `MemoryProtocol` | Task-start, in-task, and task-end Memory lifecycle boundary |
| `TaskNotesProtocol` | Task-local working-record boundary used by the loop |
| `TaskNotes` | Goal, phase, and compact Tool-result timeline with optional file mirror |
| `TaskNotesSnapshot` | Typed completed or in-progress notes supplied to consolidation |
| `FileMemory` | File-backed Task Notes, durable Memory, and Tool Experience |
| `MemorySection` | Controlled `preferences`, `conventions`, or `general_lessons` section type |
| `DurableMemoryEntry` | One candidate cross-task entry for `MEMORY.md` |
| `ToolExperienceOutcome` | `success` or `failure` ledger section type |
| `ToolExperienceEntry` | One candidate lesson keyed by Tool and outcome |
| `MemoryConsolidation` | Typed pair of Memory entries and Tool Experience entries |
| `MemoryConsolidatorProtocol` | Extract durable candidates from completed Task Notes |
| `ModelMemoryConsolidator` | Reference model-backed consolidation implementation |
| `ToolExperienceSummarizerProtocol` | Summarize one Tool ledger for its Tool Definition |
| `ModelToolExperienceSummarizer` | Reference model-backed Tool Experience summarizer |
| `InMemoryTaskNotes` | Task Notes without an operator-facing mirror file |
| `NullMemory` | No-persistence implementation used by standalone Tasks |

## Embodiment Profile

| API | Role |
|---|---|
| `EmbodimentProfile` | Validated stable body-specific Markdown document |
| `EMBODIMENT_PROFILE_ITEMS` | Canonical three sections and seven required item names |
| `embodiment_profile_path_from_config()` | Resolve the configured deployment-owned profile path |
| `load_embodiment_profile_document_from_config()` | Load and optionally validate the selected profile |

## Minimal imports

```python
from harness import (
    Harness,
    Observation,
    SceneGraphBrief,
    ToolRegistry,
)
```

Inspect an installed signature and its annotations directly:

```bash
python -c 'import inspect, harness; print(inspect.signature(harness.Harness))'
```
