# Observation and Safety

## Current Observation

An `ObservationProvider` receives an `ObservationSink` and the current Turn.
The Harness invokes it before every model decision. Observation capture is a
default runtime operation, not a model-visible Tool.

```python
from harness import Observation, VisualEvidence, publish_observation


def observe(sink, turn):
    return publish_observation(
        sink,
        Observation(
            visuals=(
                VisualEvidence(
                    name="front",
                    data=read_front_camera_jpeg(),
                    media_type="image/jpeg",
                    caption="Current front camera",
                    freshness="current",
                ),
            ),
            captured_at=current_timestamp(),
            provenance="robot_camera",
        ),
        turn=turn,
    )
```

`VisualEvidence` accepts encoded image bytes or an HTTP(S) or data URL. Local
sensor paths are not part of the public Observation boundary. The latest
Observation is valid for one model decision and replaces the prior one.

Observation and Scene Graph have different lifetimes. Observation carries
current local evidence. The working Scene Graph retains persistent refs and
coarse world state across Turns. See the
[Scene Graph guide](https://eit-hai.github.io/thea/documentation/scene-graph/).

## Base-motion safety

`BaseMotionSafetyFilter` recognizes `move_base` and `navigate_to`. When either
Tool is registered, it calls a deployment-supplied `BaseClearanceProvider`
before each model decision and adds the four-direction reading to the current
Refreshed Observation. Before executable base motion, it calls the provider
again and clamps supported `move_base` translations to the admissible
distance. Both readings are internal runtime operations rather than
model-visible Tools.

The provider returns the typed clearance record used in Observation:

```python
from harness import BaseClearance


def read_base_clearance():
    scan = robot.read_lidar_clearance()
    return BaseClearance(
        forward_m=scan.forward_m,
        backward_m=scan.backward_m,
        left_m=scan.left_m,
        right_m=scan.right_m,
    )
```

A `navigate_to` call names a target rather than a translation direction. The
filter blocks it only when no direction has enough immediate clearance.
Path planning and continuous collision avoidance remain the navigation
stack's responsibility.

For another robot or safety architecture, implement `PreExecutionHook`. The
hook returns a `PreExecutionHookResult` that either preserves or rewrites the
call, emits structured events, or blocks execution with a Tool Result:

```python
from harness import Harness, PreExecutionHookResult, ToolHooks


class WorkspaceHook:
    def apply(self, call, *, registry, turn):
        del registry
        if violates_workspace_limit(call):
            return PreExecutionHookResult(
                call,
                events=(
                    {
                        "type": "safety_check",
                        "turn": turn,
                        "success": False,
                    },
                ),
                blocked_result={
                    "success": False,
                    "reason": "The requested motion exceeds the workspace limit.",
                },
            )
        return PreExecutionHookResult(call)


hooks = ToolHooks(registry, config, pre_execution_hook=WorkspaceHook())
harness = Harness(config, registry=registry, hooks=hooks)
```

An explicit pre-execution hook replaces the default `BaseMotionSafetyFilter`;
it cannot be combined with `base_clearance_provider=` on the same Harness.
Harness checks do not replace low-level collision avoidance, emergency stops,
actuator limits, or authorization.
