# Thea Simulation Adapters

`thea-simulation` connects a user-managed benchmark episode to the same Thea
Harness interfaces used by a physical robot. It projects simulator cameras
into current Observation, exposes policy-backed primitive Tools, retains
post-execution evidence, and maps benchmark success to Evaluation as Exit
Codes.

## Choose a Benchmark

| Benchmark | Episode adapter | Wrapped upstream API |
|---|---|---|
| [LIBERO](https://lifelong-robot-learning.github.io/LIBERO/) | `LiberoEpisode` | `OffScreenRenderEnv`: `reset`, `step`, and `check_success` |
| [RoboTwin 2.0](https://robotwin-platform.github.io/) | `RoboTwinEpisode` | Evaluation task: `setup_demo`, `get_obs`, `take_action`, and `check_success` |

Install and configure the selected benchmark first, then pass the environment
object created by that benchmark to the matching adapter.

## Install

For a channel-hosted simulator launched with the root `run.sh`, install all
public packages from the repository root:

```bash
./install.sh
```

For a library-only integration in an existing Python environment, install the
Harness and simulation package directly:

```bash
python -m pip install './harness[anthropic]'
python -m pip install ./simulation
```

Replace `anthropic` with the model extra used by the deployment, or use
`harness[all]`.

The distribution name is `thea-simulation`; Python code imports
`thea_simulation`.

## Connect an Episode

Both adapters implement `SimulationEpisode`:

```python
episode.reset(seed=seed)
raw_observation = episode.observe()
transition = episode.step(action)
finished = transition.task_success
episode.close()
```

This boundary deliberately separates a benchmark episode from Thea Tools.
LIBERO and RoboTwin expose continuous robot actions; they do not provide
semantic tools such as `pick_object`. A deployment must connect each primitive
tool to a policy that yields an action chunk.

`SimulationRuntime` performs that connection. It:

1. projects the latest benchmark observation into `harness.Observation`;
2. registers simulator-backed primitive tools in `ToolRegistry`;
3. retains each tool run's post-execution evidence under its `run_id`;
4. exposes that evidence to `SimulationTaskEvaluator`.

The evaluator returns `process` while an episode remains active, `success`
when the benchmark reports task success, and `failure` when the episode ends
without satisfying the benchmark condition. Simulator-backed action tools
must therefore appear in both `evaluation.required_tools` and
`evaluation.segment_tools`.

## LIBERO

LIBERO can be created through its task-suite API:

```python
from thea_simulation import (
    LiberoEpisode,
    SimulationRuntime,
    project_libero_observation,
)

episode = LiberoEpisode.from_suite(
    "libero_10",
    task_id=0,
    initial_state_id=0,
    seed=0,
    env_kwargs={
        "camera_heights": 128,
        "camera_widths": 128,
    },
)

runtime = SimulationRuntime(
    episode,
    observation_projector=project_libero_observation,
    tools=(),
)
```

`LiberoEpisode` preserves LIBERO's fixed initial states and sparse task-success
signal. `project_libero_observation` exposes every HxWx3 field whose name ends
in `_image` as named `VisualEvidence`. A standard LIBERO observation therefore
includes both `agentview` and `robot0_eye_in_hand`; deployments that configure
additional RGB cameras receive those views without changing the projector.
Non-image state, depth, and proprioceptive arrays remain outside this visual
projection. The default encoder rotates LIBERO's raw camera arrays into their
displayed orientation and encodes them as PNG.

## RoboTwin

RoboTwin task construction depends on task YAML, embodiment assets, and domain
randomization settings. Keep that setup in one callback:

```python
from thea_simulation import RoboTwinEpisode, project_robotwin_observation


def setup(task_env, seed):
    task_env.setup_demo(
        now_ep_num=0,
        seed=seed,
        is_test=True,
        **task_config,
    )
    return task_env.get_obs()


episode = RoboTwinEpisode(
    task_env,
    task_id="pick_dual_bottles:demo_clean:0",
    instruction="Pick up both bottles.",
    setup=setup,
    action_type="qpos",
)

observation = project_robotwin_observation(episode.observe(), turn=1)
```

The adapter also accepts RoboTwin `ee` and `delta_ee` action modes. The
default projector exposes every RGB view under
`observation.<camera_name>.rgb`, plus `third_view_rgb` when configured. Camera
calibration, robot state, depth, and point clouds remain available in the raw
benchmark observation. The primitive policy supplies actions in the configured
control mode.

## Register a Primitive Tool

```python
from harness import Observation, ToolRegistry
from thea_simulation import (
    SimulationRuntime,
    SimulationTaskEvaluator,
    action_chunk_tool,
)


def project(raw_observation, turn):
    return Observation(
        visuals=encode_benchmark_cameras(raw_observation),
        captured_at=turn,
        provenance=episode.benchmark,
    )


def pick_policy(active_episode, arguments):
    target = arguments["target"]
    observation = active_episode.observe()
    yield from primitive_policy.pick(target, observation)


pick_object = action_chunk_tool(
    name="pick_object",
    description="Pick one target identified by the current task.",
    input_schema={
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
        "additionalProperties": False,
    },
    post_condition="The benchmark task success condition is satisfied.",
    policy=pick_policy,
)

runtime = SimulationRuntime(
    episode,
    observation_projector=project,
    tools=(pick_object,),
)
runtime.reset(seed=0)

registry = ToolRegistry()
runtime.register_tools(registry)
```

Supply `runtime.observation_provider`, `runtime` as the
`post_execution_observation_provider`, and `SimulationTaskEvaluator()` to
`Harness`. Add `runtime` to `owned_resources` so the simulator closes with the
Harness.

## Compose and Run

The deployment factory combines the runtime with a model and the Harness. Any
Tool whose policy can return a nonterminal `process` verdict must be listed in
both `evaluation.required_tools` and `evaluation.segment_tools`:

```python
from harness import Harness, ToolRegistry
from thea_simulation import SimulationTaskEvaluator


def create_harness(config, session_context):
    del session_context
    episode = build_episode()
    runtime = build_runtime(episode)
    runtime.reset(seed=0)

    registry = ToolRegistry()
    runtime.register_tools(registry)

    return Harness(
        config,
        model=build_model(config),
        registry=registry,
        observation_provider=runtime.observation_provider,
        evaluator=SimulationTaskEvaluator(),
        post_execution_observation_provider=runtime,
        owned_resources=(runtime,),
    )
```

For a `pick_object` policy, the corresponding configuration includes:

```yaml
evaluation:
  required_tools: [pick_object]
  segment_tools: [pick_object]
```

Launch the importable factory from the repository root:

`my_runtime.sim:create_harness` is an example import path. Replace it with the
module and callable implemented for your benchmark deployment. The root
`simulation` mode hosts this factory through Feishu/Lark, so the channel
credentials must also be configured. These commands assume the repository
installer created the root `.venv`:

```bash
./run.sh --mode simulation --check \
  --harness-factory my_runtime.sim:create_harness

./run.sh --mode simulation \
  --harness-factory my_runtime.sim:create_harness
```

For a simulator application without Lark, construct the same `Harness` in the
application process and call `run_stream()` directly; `SimulationRuntime` and
the episode adapters do not depend on the channel.

## Verify the Adapters

The package test suite uses protocol substitutes rather than importing either
benchmark. These substitutes run through the real `SimulationRuntime`,
`ToolRegistry`, Agentic Loop, action-segment continuation, and evaluator:

```bash
python -m pip install -e './simulation[test]'
python -m pytest -q simulation/tests
```

This verifies Thea's adapter contracts and control flow without installing
MuJoCo, SAPIEN, or benchmark assets. It does not verify compatibility with a
specific upstream benchmark revision. After installing a benchmark, also run
one reset-observe-step-close episode against the selected revision.

See the repository
[contribution guide](https://github.com/EIT-HAI/Thea/blob/main/CONTRIBUTING.md)
for development requirements. The package uses Apache License 2.0; see
[LICENSE](https://github.com/EIT-HAI/Thea/blob/main/simulation/LICENSE) and
[NOTICE](https://github.com/EIT-HAI/Thea/blob/main/simulation/NOTICE).
