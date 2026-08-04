# Port to Your Robot

Thea keeps robot-specific SDKs, policies, and sensing backends outside the
Harness package. A deployment connects those capabilities through public
protocols and returns one `Harness` from an importable factory.

Start from
[`examples/port-template/`](https://github.com/EIT-HAI/Thea/tree/main/examples/port-template).
Copy the directory to an importable package such as `my_robot`, then replace
its explicit adapter boundaries.

## The three deployment files

| File | Responsibility |
|---|---|
| `my_robot/profile.md` | Record stable properties of the active embodiment. |
| `my_robot/tools.py` | Register navigation and manipulation tools through `ToolRegistry`. |
| `my_robot/factory.py` | Connect resources and compose the public Harness dependencies. |

### 1. Describe the embodiment

The Embodiment Profile has three validated sections:

| Section | Required items |
|---|---|
| Operational Envelope | Base Footprint, Base Mobility, Reachable Workspace |
| Perception Configuration | Sensor Modalities, Model-Visible Views |
| Base-Relative Positions | Camera Positions, Initial Gripper Positions |

Select the copied file in `config.yaml`:

```yaml
embodiment_profile_file: my_robot/profile.md
```

The profile contains stable body-specific context. Current robot pose,
clearance, images, and task state belong to Refreshed context instead.

### 2. Register physical tools

Register each capability with a precise name, description, input schema, and
post-condition when the tool requires Evaluation as Exit Codes:

```python
registry.register(
    BuiltinTool(
        name="pick_object",
        description="Pick one confirmed object ref using current evidence.",
        input_schema=pick_schema,
        fn=robot.pick_object,
        post_condition="The requested object is secured by the robot.",
    )
)
```

An in-process SDK adapter may use `BuiltinTool`. A separately deployed policy
server may expose the same tool definition through `MCPTool`. The model sees the
same protocol in either case.

### 3. Compose the factory

The Lark runtime calls the factory once per channel session with the validated
configuration and a `ChannelSessionContext`. The factory must return one
`Harness`:

```python
def create_harness(config, session_context):
    robot = connect_robot()
    registry = ToolRegistry()
    register_robot_tools(registry, robot)

    scene_graph = build_scene_graph(robot)
    register_scene_graph_query_tools(registry, scene_graph)

    return Harness(
        config,
        model=build_model(config),
        observation_provider=build_observation_provider(robot),
        base_clearance_provider=build_base_clearance_provider(robot),
        registry=registry,
        scene_graph=scene_graph,
        evaluator=build_evaluator(config),
        post_execution_observation_provider=build_evidence_provider(robot),
        owned_resources=(robot,),
    )
```

`owned_resources` are closed with the Harness session. Keep shared process-wide
resources outside this tuple and manage their lifetime in the deployment.

## Launch the deployment

The factory path uses `module:callable` syntax. The root `robot` mode is a
Feishu/Lark-hosted deployment, so configure the channel credentials before
launching it:

```bash
./run.sh --mode robot \
  --harness-factory my_robot.factory:create_harness
```

Use `--check` first to validate imports and configuration without constructing
robot resources:

```bash
./run.sh --mode robot --check \
  --harness-factory my_robot.factory:create_harness
```

An application that does not use Lark should compose the same dependencies in
its own process, construct `Harness`, and call `run_stream()` directly instead
of using `--mode robot`.

## Connect boundaries incrementally

Use the [Deployment Guide](deployment.md) to replace one boundary at a time.
A practical order is model, one read-only tool, Observation, Scene Graph,
physical tools, post-execution evidence, evaluator, Memory and Skills, then the
user channel.

The Harness safety checks are orchestration safeguards, not a certified safety
system. Emergency stops, actuator limits, collision avoidance, and low-level
motion control must remain independent of the model.
