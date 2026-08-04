# Evaluation

Evaluation as Exit Codes reconstructs the outcome signal that physical
execution does not provide automatically. After a configured physical Tool
finishes, the Harness invokes an independent evaluator through a hidden
post-execution hook.

The evaluator receives only:

1. the selected Tool's post-condition; and
2. a post-execution `Observation` associated with that Tool's `run_id`.

It does not receive the acting model's reasoning, self-report, active
instruction, or backend result fields.

## What the public release provides

| Thea provides | A deployment provides |
|---|---|
| Hidden `evaluate_run` Tool and structural post-execution trigger | A non-empty `run_id` from every evaluated physical Tool |
| `EvaluatorProtocol` and typed three-state verdict | A custom evaluator or a model configured for `ModelEvaluator` |
| `PostExecutionObservationProvider` | Resolution from `run_id` to objective post-execution evidence |
| Per-Tool post-condition boundary | The actual success criterion for each physical Tool |
| Validation for `process`, `success`, and `failure` | Segment semantics for policies that execute incrementally |
| Success-gated Scene Graph update | Deployment-specific execution-derived graph changes |

The public package does not include robot artifact directories, policy log
formats, camera synchronization, or a universal set of post-conditions.

## Verdict contract

```python
from harness import EvaluatorVerdict

verdict = EvaluatorVerdict(
    status="failure",
    evidence=(
        "the bottle remains on the table",
        "the gripper closed without lifting it",
    ),
    failure_reason="the robot stopped too far from the bottle to grasp it",
)
```

`status` is one of:

- `process`: execution should continue with another action segment;
- `success`: the post-condition is satisfied;
- `failure`: execution should stop and the failure reason returns to the
  Agentic Loop.

`process` is accepted only for a Tool listed in
`evaluation.segment_tools`.

## Attach a post-condition

An in-process Tool carries the criterion directly:

```python
from harness import BuiltinTool

registry.register(
    BuiltinTool(
        name="pick_object",
        description="Pick one confirmed Scene Graph ref.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        fn=pick_object,
        post_condition=(
            "The requested object is visibly lifted and held by the gripper."
        ),
    )
)
```

For an MCP Tool, map the same contract by name:

```yaml
evaluation:
  required_tools: [pick_object]
  segment_tools: []
  max_segments: 8
  post_conditions:
    pick_object: >-
      The requested object is visibly lifted and held by the gripper.
```

Every evaluated Tool must return a non-empty `run_id`, even when its own
backend reports failure. Backend status is not the physical verdict.

## Connect post-execution evidence

The deployment decides how a `run_id` resolves to evidence:

```python
from harness import Observation


class MyPostExecutionObservationProvider:
    def capture(self, run_id: str) -> Observation:
        return capture_observation_for_run(run_id)
```

The returned `Observation` is path-free and provider-neutral. It may carry
named camera views, base clearance, timestamps, provenance, and a compact
summary. `ModelEvaluator` forwards up to three named visual views by default.

## Choose an evaluator

Use the included model-backed evaluator:

```python
from harness import ModelEvaluator
from harness.models import build_model

evaluator = ModelEvaluator(
    build_model(
        "openai",
        model="gpt-4o-mini",
    )
)
```

Or implement a deterministic, learned, or service-backed evaluator:

```python
from harness import EvaluationRequest, EvaluatorVerdict


class MyEvaluator:
    def evaluate(self, request: EvaluationRequest) -> EvaluatorVerdict:
        return judge_post_condition(
            request.post_condition,
            request.post_execution_observation,
        )
```

The implementation must return `EvaluatorVerdict`.

## Compose the Harness

```python
harness = Harness(
    config,
    registry=registry,
    evaluator=evaluator,
    post_execution_observation_provider=(MyPostExecutionObservationProvider()),
    scene_graph=scene_graph,
)
```

The resulting call chain is:

```text
evaluated physical Tool
  -> automatic post-execution hook
  -> hidden evaluate_run(run_id, target_tool_name)
  -> PostExecutionObservationProvider.capture(run_id)
  -> EvaluatorProtocol.evaluate(post-condition, Observation)
  -> process | success | failure
```

The model cannot select `evaluate_run`. The Harness derives the call from the
completed physical Tool and hides its Tool Definition from the model.

## Reproduce Evaluation as Exit Codes

To reproduce the complete behavior on another robot or policy:

1. Define a visible, testable post-condition for each evaluated Tool.
2. Make the Tool return a stable `run_id` after physical execution begins.
3. Retain the evidence needed to judge that run.
4. Implement `capture(run_id)` to return a typed post-execution
   `Observation`.
5. Configure `ModelEvaluator` or implement `EvaluatorProtocol`.
6. Add the Tool to `evaluation.required_tools`.
7. For chunked policies, also add it to `evaluation.segment_tools` and set an
   appropriate `max_segments`.
8. Confirm that only evaluator `success` reaches
   `SceneGraphProtocol.apply_confirmed_execution()`.

For a camera-based evaluator, reproduce the camera synchronization and view
selection in the deployment package. For a simulator-state or learned-value
evaluator, translate that evidence behind `EvaluatorProtocol`; do not leak
privileged state into the acting model's context unless the experiment
explicitly intends to do so.

## Verify the integration

Exercise one Tool through the full Agentic Loop and inspect the emitted events:

1. the physical Tool returns one `run_id`;
2. the hidden evaluator starts exactly once per completed segment;
3. the evaluator receives the configured post-condition and matching
   post-execution Observation;
4. malformed verdicts fail closed;
5. `failure` returns evidence and a non-empty failure reason to the model;
6. `success` permits the configured execution-derived Scene Graph update.

Calls rejected before execution by argument validation or a pre-execution hook
do not enter Evaluation.
