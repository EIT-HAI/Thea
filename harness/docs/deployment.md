# Deployment Guide

## Recommended replacement order

Replace one boundary at a time and rerun the focused tests after each step:

1. replace the deterministic model with `ModelProtocol`;
2. register one read-only deployment tool;
3. publish current evidence through `ObservationProvider`;
4. connect the working graph through `SceneGraphProtocol`;
5. register ref-keyed Scene Graph queries;
6. add one physical tool and its post-condition;
7. connect post-execution evidence and `EvaluatorProtocol`;
8. add Memory, Skills, and an Embodiment Profile;
9. attach a user channel.

This order keeps early failures outside physical execution and makes each new
boundary independently observable.

## Composition shape

```python
from harness import (
    Harness,
    ToolRegistry,
    register_scene_graph_query_tools,
)


def build_harness(config):
    robot = connect_robot()
    registry = ToolRegistry()
    register_robot_tools(registry, robot)

    scene_graph = build_scene_graph(robot)
    register_scene_graph_query_tools(registry, scene_graph)

    return Harness(
        config,
        model=build_model(config),
        memory=build_memory(config),
        observation_provider=build_observation_provider(robot),
        base_clearance_provider=build_base_clearance_provider(robot),
        registry=registry,
        scene_graph=scene_graph,
        evaluator=build_evaluator(config),
        post_execution_observation_provider=build_post_execution_observer(robot),
        owned_resources=(robot,),
    )
```

The deployment package owns every helper in this example. The open Harness
does not import a robot-specific adapter.

## Configuration

The packaged baseline is intentionally independent of a robot:

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  api_key_env: ANTHROPIC_API_KEY
  max_tokens: 4096

context:
  compaction:
    enabled: true
    context_window: 200000
    reserve_tokens: 16384
    keep_recent_turns: 4
    keep_recent_tokens: 20000
    tool_result_max_chars: 2000
    reasoning_max_chars: 2000
    max_input_chars: 400000
    max_summary_rounds: 8

safety:
  base_clearance_margin_m: 0.05

evaluation:
  required_tools: []
  segment_tools: []
  max_segments: 8
  post_conditions: {}

servers: []
```

| Section | Required | Purpose |
|---|---:|---|
| `llm` | when `model=` is omitted | Select and configure a built-in model adapter. |
| `servers` | no | Trusted stdio MCP process definitions. |
| `context.compaction` | no | Configure the model-context budget, reserved response space, retained recent tail, and bounded checkpoint generation. |
| `evaluation` | no | Name evaluated and segmented tools and map MCP post-conditions. |
| `safety.base_clearance_margin_m` | no | Reserve clearance for the default base-motion filter. |
| `paths.base_dir` | no | Resolve deployment-owned relative paths from one explicit root. |
| `skills.dir` | no | Select a directory-backed Skill catalog. |
| `embodiment_profile_file` | no | Select the active Embodiment Profile. |

Relative Skill and Embodiment Profile paths resolve against
`paths.base_dir`, when present, then the process working directory. Package and
source-checkout locations are never inferred.

## Base-motion safety hook

The default `BaseMotionSafetyFilter` recognizes `move_base` and `navigate_to`.
When either Tool is registered, it reads four-direction clearance through the
deployment's `BaseClearanceProvider` before each model decision and adds the
reading to Refreshed context. Before executable base motion, it reads the
provider again and clamps supported `move_base` translations to the admissible
distance. The provider is an internal runtime boundary, not a Tool. A
`navigate_to` call names a target rather than a translation direction, so the
filter blocks it only when no direction has enough immediate clearance; path
planning and continuous collision avoidance remain the navigation stack's
responsibility.
For `move_base`, only the explicit mode `check_only` skips this pre-execution
refresh. A missing or unrecognized mode is treated as executable motion and
therefore fails safe through the same clearance check. A deployment that
registers base-motion Tools without a `BaseClearanceProvider` fails closed.

For another robot or safety architecture, implement `PreExecutionHook`.
Harness checks do not replace low-level collision avoidance, emergency stops,
actuator limits, or authorization.

## Lifecycle and cancellation

- One `Harness` instance rejects concurrent tasks.
- `reset_session()` clears Accumulated context while retaining clients and
  tools.
- `run_stream(..., should_cancel=callback)` checks cooperative cancellation
  before a decision and before execution.
- `run_stream(..., poll_replan=callback)` accepts an instruction revision
  inside an active task.
- `close()` releases owned MCP processes and `owned_resources`.
- A blocking physical tool must expose its own interrupt and emergency-stop
  path.

## Run events and logs

`run_stream()` yields provider-neutral dictionaries for context refresh,
listed tools, Observations, prompt snapshots, model decisions, Tool Calls,
Tool Results, hooks, evaluator verdicts, compaction, Memory consolidation, and
task outcome.

`harness.observability.run_logger()` writes JSONL under:

1. `THEA_HARNESS_LOG_DIR`, when set;
2. `$XDG_STATE_HOME/thea-harness/runs`;
3. `~/.local/state/thea-harness/runs`.

Logs use owner-only permissions, redact common credential fields and inline
image data, and bound individual events. They can still contain user text,
model reasoning, tool arguments, and physical observations.

## Troubleshooting

| Symptom | Check |
|---|---|
| `Environment variable ... is not set` | Export the credential named by `llm.api_key_env`, or inject `ModelProtocol`. |
| Embodiment Profile not found | Remove the optional setting or resolve it below the working directory or `paths.base_dir`. |
| Mock provider requires `replay_path` | Set `llm.replay_path` or `LLM_REPLAY` to a JSONL decision file. |
| MCP calls remain blocked after timeout | Stop physical execution, inspect the robot, and restart the affected runtime. |
| Tool protocol failure | Match `inputSchema` and return a documented success/failure envelope. |
