# Usage

Thea is a provider-neutral Harness for embodied agents. It owns the Agentic
Loop, context lifetimes, Tool execution, post-execution Evaluation, and the
boundaries through which a deployment supplies physical-world evidence.

The public release can be used in five ways:

| Deployment | What you provide | What Thea provides |
|---|---|---|
| Local terminal | A configured model credential | The complete Agentic Loop and terminal-backed user interaction without Lark or a robot |
| Feishu/Lark channel | App credentials, authorized users, and a configured model | Per-user Harness sessions, cards, images, clarification, and notifications |
| Harness library | A model and model-visible Tools | Agentic Loop, Context, Tool Protocol, Memory, Skills, hooks, and logs |
| Simulator | A LIBERO or RoboTwin 2.0 environment and primitive policy | Observation projection, policy-backed Tool integration, and evaluator wiring |
| Physical robot | Robot SDK adapters, sensing, policies, Scene Graph, and post-execution evidence | The same runtime and public interfaces used in simulation |

The root launcher maps these paths to four modes:

| Command mode | Process started | Additional requirement |
|---|---|---|
| `--mode cli` | Interactive terminal, or one instruction with `--instruction` | Model configuration and credential |
| `--mode channel` | Feishu/Lark channel with the default Harness | Model and Lark credentials |
| `--mode simulation` | Feishu/Lark channel with a simulator Harness factory | Lark credentials, importable factory, and `thea-simulation` |
| `--mode robot` | Feishu/Lark channel with a robot Harness factory | Lark credentials and importable factory |

`simulation` and `robot` are channel-hosted deployment modes, not standalone
simulator or hardware executables. Applications without Lark compose `Harness`
in Python and call `run_stream()` directly. Run `./run.sh --help` for the
complete launcher options.

## Choose a starting point

### Run the Harness in a terminal

Install the repository, configure a real model in `.env` and `config.yaml`,
then start the interactive terminal:

```bash
./install.sh
./run.sh --mode cli --check
./run.sh --mode cli
```

This path requires no Lark credential and no simulator or physical robot. It
also supports a single instruction:

```bash
./run.sh --mode cli --instruction \
  "Notify the user that the Harness is ready, then explain what ran."
```

### Run the complete channel

Install all public packages, configure a model and Feishu/Lark credentials,
then start the channel:

```bash
./install.sh
./run.sh --mode channel --check
./run.sh --mode channel
```

The channel creates one Harness session per authorized user and registers
`query_user` and `notify_user` as model-visible Tools.

### Connect a simulator

Install the selected benchmark environment separately, build a factory that
returns one `Harness`, configure the Lark credentials described above, and run
the preflight before starting the channel:

`my_runtime.sim:create_harness` is an example import path. Implement it using
the simulation guide before running this command:

```bash
./run.sh --mode simulation --check \
  --harness-factory my_runtime.sim:create_harness

./run.sh --mode simulation \
  --harness-factory my_runtime.sim:create_harness
```

The factory connects the benchmark episode, primitive policy Tools,
Observation provider, and evaluator. See [Simulation](../reference/simulation.md)
for the LIBERO and RoboTwin 2.0 adapter boundaries.

### Connect a physical robot

Keep hardware SDKs and deployment-specific code outside the Harness package.
Expose them through the public interfaces, configure the Lark credentials, and
validate the factory before starting the channel:

`my_runtime.robot:create_harness` is an example import path and must resolve to
the factory in your deployment package:

```bash
./run.sh --mode robot --check \
  --harness-factory my_runtime.robot:create_harness

./run.sh --mode robot \
  --harness-factory my_runtime.robot:create_harness
```

The factory owns the robot connection and must return one configured
`Harness`. Start from [Port to Your Robot](../harness/port-to-your-robot.md),
then connect [Scene Graph](../scene-graph/index.md) and
[Evaluation](../evaluation/index.md) when physical execution is ready.

## Extend an Existing Harness

Use the smallest public boundary that matches the new capability:

| Goal | First step | Detailed guide |
|---|---|---|
| Change the model | Configure `llm` or implement `ModelProtocol`. | [Models and Tools](../harness/model-and-tools.md) |
| Add a Tool | Register an annotated callable, `BuiltinTool`, or MCP capability with `ToolRegistry`. | [Models and Tools](../harness/model-and-tools.md) |
| Add current evidence | Implement `ObservationProvider` and, when needed, `BaseClearanceProvider`. | [Observation and Safety](../harness/observation-and-safety.md) |
| Add reusable task guidance | Create a directory-backed `SKILL.md` and select `skills.dir`. | [Memory, Skills, and Embodiment](../harness/memory-and-skills.md) |
| Add persistent world state | Implement `SceneGraphProtocol` and its ref-keyed queries. | [Scene Graph](../scene-graph/index.md) |
| Add physical outcome checks | Connect post-execution evidence and `EvaluatorProtocol`. | [Evaluation](../evaluation/index.md) |

## How one task runs

One user instruction starts one Task. Each Turn follows the same reactive
cycle:

```text
refresh evidence
  -> model decision
  -> execute at most one selected Tool
  -> run configured hooks
  -> record the result
  -> refresh again
```

The model may return text together with one Tool Call. A Task ends when the
model returns no Tool Call or when the Harness terminates the run. The final
text becomes the task report.

## Recommended reading order

1. [Install & Setup](../setup/index.md)
2. [Harness](../harness/index.md)
3. [Port to Your Robot](../harness/port-to-your-robot.md)
4. [Scene Graph](../scene-graph/index.md)
5. [Evaluation](../evaluation/index.md)
6. [Configuration](../reference/configuration.md)
7. [API](../reference/api.md)
