# Harness

The Harness is the provider-neutral runtime between the model and the active
embodiment. It owns orchestration and context. A deployment owns robot or
simulator connections, sensing backends, policies, and hardware safety.

## Components

| Component | Responsibility |
|---|---|
| [Agentic Loop](concepts.md) | Refresh evidence, request one model decision, execute at most one selected Tool, and continue from its result. |
| [Context](concepts.md) | Keep Resident, Refreshed, and Accumulated information at separate lifetimes. |
| [Tool Protocol](model-and-tools.md) | Publish Tool Definitions, validate arguments, normalize Tool Results, and execute hooks. |
| [Memory](memory-and-skills.md) | Maintain Task Notes, durable Memory, and Tool Experience. |
| [Skills](memory-and-skills.md) | Keep Skill metadata Resident and load matched instructions on demand. |
| [Observation](observation-and-safety.md) | Supply current visual evidence and, when base-motion support is configured, four-direction clearance for one decision. |
| [Safety](observation-and-safety.md) | Apply deterministic pre-execution checks below the model. |
| [User Interaction](../usage/index.md) | Provide terminal or Feishu/Lark implementations of `query_user` and `notify_user`. |
| [Embodiment Profile](memory-and-skills.md) | Present stable capabilities, sensing configuration, and base-relative positions. |

Scene Graph and Evaluation are first-class physical-world components with
their own guides:

- [Scene Graph](../scene-graph/index.md) makes the world readable and
  ref-addressable.
- [Evaluation](../evaluation/index.md) judges physical outcomes after Tool
  execution.

## Public composition boundary

The main composition root is `Harness`. Its constructor accepts public
protocols rather than importing a robot-specific runtime:

```python
from harness import Harness

harness = Harness(
    config,
    model=model,
    registry=registry,
    observation_provider=observation_provider,
    base_clearance_provider=base_clearance_provider,
    scene_graph=scene_graph,
    evaluator=evaluator,
    post_execution_observation_provider=post_execution_observer,
    owned_resources=(robot,),
)
```

Start from [Port to Your Robot](port-to-your-robot.md) for a copyable
integration path. Use the [Deployment Guide](deployment.md) for configuration,
resource ownership, cancellation, logging, and failure handling.

```{toctree}
:hidden:
:maxdepth: 2

concepts
model-and-tools
observation-and-safety
memory-and-skills
port-to-your-robot
deployment
```
