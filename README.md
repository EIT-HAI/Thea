<h1 align="center">Towards the Harness of Embodied Agents</h1>

<p align="center">
  <a href="https://eit-hai.github.io/thea/"><img src="docs/assets/button-project-page-globe.svg" alt="Open the project page" height="30"></a>&ensp;
  <a href="https://eit-hai.github.io/thea/paper.pdf"><img src="docs/assets/button-paper.svg" alt="Read the paper" height="30"></a>&ensp;
  <a href="https://youtu.be/Sm9jFmfnOF0"><img src="docs/assets/button-youtube.svg" alt="Watch the demo on YouTube" height="30"></a><a href="https://github.com/EIT-HAI/Thea/releases/download/demo-v1/act1.mp4"><img src="docs/assets/button-bilibili.svg" alt="Watch the demo on Bilibili" height="30"></a>&ensp;
  <a href="https://eit-hai.github.io/thea/documentation/"><img src="docs/assets/button-docs.svg" alt="Open the docs" height="30"></a>&ensp;
  <a href="#citation"><img src="docs/assets/button-bibtex.svg" alt="Jump to BibTeX" height="30"></a>
</p>

<h3 align="center">TL;DR: Thea brings coding agents to the physical world.</h3>

<br>

<p align="center">
  <img src="docs/assets/instruction.svg" alt="Instruction: &ldquo;Grab me a water.&rdquo;" width="760">
  <br>
  <a href="https://youtu.be/Sm9jFmfnOF0">
    <img src="https://github.com/EIT-HAI/Thea/releases/download/demo-v1/act1-preview.webp" alt="Thea demo: Grab me a water" width="760">
  </a>
</p>

## ⚡ Quick Start

Python 3.10 or newer is required.

### Terminal

Clone the repository, install the public packages, add the selected model API
key to `.env`, and run the Harness directly in a local terminal:

```bash
git clone https://github.com/EIT-HAI/Thea.git && cd Thea && ./install.sh
./run.sh --mode cli --check
./run.sh --mode cli
```

The installer creates `.venv`, installs the Harness, simulation interfaces,
model adapters, and Lark channel, and initializes configuration files. Existing
`.env` and `config.yaml` files are preserved. Terminal mode uses the real model
selected in `config.yaml`; it does not start Lark or require a simulator or
physical robot. To run one Task without entering the interactive prompt:

```bash
./run.sh --mode cli --instruction \
  "Notify the user that the Harness is ready, then explain what ran."
```

### Feishu/Lark

Add the selected model API key and Lark application credentials to `.env`,
review `config.yaml`, then validate and start the channel:

```text
LARK_APP_ID=cli_...
LARK_APP_SECRET=...
LARK_ALLOWED_OPEN_IDS=ou_...
```

```bash
./run.sh --mode channel --check
./run.sh --mode channel
```

The default WebSocket transport does not require a public callback URL. The
channel adds session-aware `query_user` and `notify_user` Tools. See the
[Lark guide](lark/README.md) for application setup, webhook transport, cards,
authorization, and deployment factories.

### Simulation

Install either [LIBERO](https://lifelong-robot-learning.github.io/LIBERO/) or
[RoboTwin 2.0](https://robotwin-platform.github.io/), connect one episode and
its primitive policies through a Harness factory, then run the commands below.
`my_runtime.sim:create_harness` is an example import path; implement that
factory by following the simulation guide before running it. This mode hosts
the simulator-backed Harness through the Feishu/Lark channel, so configure the
Lark credentials from the preceding section as well:

```bash
./run.sh --mode simulation --check \
  --harness-factory my_runtime.sim:create_harness

./run.sh --mode simulation \
  --harness-factory my_runtime.sim:create_harness
```

See the [simulation guide](simulation/README.md) for the episode, Observation,
policy Tool, and evaluator interfaces. A non-Lark application can instead
compose the same `Harness` in Python and call `run_stream()` directly.

### Physical Robot

Keep robot SDKs and policy backends in a deployment package. Start from
[`examples/port-template/`](examples/port-template/), then validate and launch
its importable factory. `my_robot.factory:create_harness` is an example import
path that must refer to the factory in your deployment package. Like simulation
mode, robot mode starts the deployment through Feishu/Lark and therefore
requires the channel credentials above:

```bash
./run.sh --mode robot --check \
  --harness-factory my_robot.factory:create_harness

./run.sh --mode robot \
  --harness-factory my_robot.factory:create_harness
```

The [Port to Your Robot](docs/harness/port-to-your-robot.md) guide covers the
Embodiment Profile, Tools, Observation, Scene Graph, Evaluation, and resource
ownership.

### Harness Only

For applications that need only the Harness:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install './harness[anthropic]'
cp harness/config.example.yaml config.yaml
export ANTHROPIC_API_KEY=...
thea-cli --config config.yaml
```

## 🔧 Build with the Harness

The Python interface runs instruction-driven Tasks and exposes focused
extension points for capabilities and task procedures.

### Run an instruction

Once a `Harness` is configured, one instruction starts one task. Streamed
events expose model decisions, Tool Calls, Tool Results, evaluation, and the
final outcome:

```python
for event in harness.run_stream("Put bottle_1 on desk_1."):
    if event["type"] == "done":
        print(event["final_text"])
```

### Add a Tool

Register an annotated Python callable for a lightweight in-process capability:

```python
from harness import ToolRegistry


def register_robot_tools(registry: ToolRegistry, robot) -> None:
    @registry.tool(description="Return the robot's current battery percentage.")
    def get_battery() -> dict:
        return {"success": True, "observation": robot.battery_percent()}
```

The Harness derives the Tool Definition from the function signature. Physical
policies can use explicit schemas, post-conditions, or MCP-backed Tools; see
[Models and Tools](harness/docs/model-and-tools.md).

### Add a Skill

A Skill packages task procedures outside the System Prompt. Its catalog entry
is visible by default, while its instructions are loaded only for a relevant
task:

```text
skills/
└── tidy-workspace/
    └── SKILL.md
```

```markdown
---
name: tidy-workspace
description: Decide what to retain, remove, and report while tidying a desk.
---

## Procedure

Inspect the workspace before moving any object.
```

Point `skills.dir` in `config.yaml` to the catalog:

```yaml
skills:
  dir: ./skills
```

The Harness keeps the name and description in Resident context and exposes
`load_skill` for the full task-scoped instructions. See
[Memory, Skills, and Embodiment](harness/docs/memory-and-skills.md) for
resources and task lifetime.

## 🧩 Harness Design

The public modules follow the terminology and boundaries of the paper:

| Paper component | Public module | Role |
|---|---|---|
| Agentic Loop | `harness/runtime/` | Executes at most one model-selected Tool per turn and continues from its Tool Result. |
| Context Engineering | `harness/context/` | Assembles Resident, Refreshed, and Accumulated context and compacts accumulated messages. |
| Tool Protocol | `harness/tools/` | Publishes Tool Definitions, validates arguments, and normalizes Tool Results. |
| Skills | `harness/skills/` | Keeps Skill metadata resident and loads an invoked Skill body for the active task. |
| Memory | `harness/memory/` | Maintains Task Notes, durable Memory, and Tool Experience at their respective lifetimes. |
| Safety | `harness/world/`, `harness/runtime/` | Refreshes clearance evidence and enforces deterministic checks below the model. |
| User Interaction | `harness/terminal/`, `lark/` | Provides terminal fallbacks and session-aware channel implementations of `query_user` and `notify_user`. |
| Scene Graph as Context | `harness/world/` | Exposes persistent ref-addressable world state, a Scene Graph Brief, and ref-keyed queries. |
| Evaluation as Exit Codes | `harness/evaluation/` | Evaluates configured physical post-conditions after tool execution. |
| Embodiment Profile | `harness/world/` | Presents stable body-specific capabilities, sensing configuration, and base-relative positions. |

The [Harness documentation](docs/harness/index.md) explains these components
and their public integration boundaries.

## 📁 Repository Structure

```text
.
├── harness/                 # Provider-neutral orchestration runtime
├── simulation/              # Optional LIBERO and RoboTwin 2.0 interfaces
├── lark/                    # Optional Feishu/Lark channel
├── examples/                # Robot port template
├── docs/                    # Documentation site
├── install.sh               # One-command installation
└── run.sh                   # Terminal, channel, simulation, and robot entry point
```

The three packages are independently installable:

- [`thea-harness`](harness/README.md) contains the core runtime and public
  deployment boundaries.
- [`thea-simulation`](simulation/README.md) contains Harness-side adapter
  interfaces for user-managed simulation environments.
- [`thea-lark`](lark/README.md) connects Harness sessions to authorized
  Feishu/Lark users.

Dependencies point inward. Lark and simulation depend on the Harness, while
the Harness does not import either integration.

## 📚 Documentation

The [documentation site](https://eit-hai.github.io/thea/documentation)
organizes the public interfaces by integration task:

- [Usage](https://eit-hai.github.io/thea/documentation/usage/index.html)
- [Install & Setup](https://eit-hai.github.io/thea/documentation/setup/index.html)
- [Harness](https://eit-hai.github.io/thea/documentation/harness/index.html)
- [Scene Graph](https://eit-hai.github.io/thea/documentation/scene-graph/index.html)
- [Evaluation](https://eit-hai.github.io/thea/documentation/evaluation/index.html)
- [Reference](https://eit-hai.github.io/thea/documentation/reference/index.html)

Package-local guides are available for the [Harness](harness/README.md),
[simulation adapters](simulation/README.md), and [Feishu/Lark channel](lark/README.md).

## 🛡️ Scope & Safety Notes

- This repository is a public preview of the provider-neutral Thea Harness,
  optional LIBERO and RoboTwin 2.0 interfaces, and the Feishu/Lark channel.
- A deployment supplies its own model credentials, robot SDK or simulator,
  policies, sensing backends, Scene Graph backend, and post-execution evidence.
- The Harness is an orchestration layer, not a certified robot safety system.
  Emergency stops, actuator limits, collision avoidance, and low-level motion
  control must remain independent of the model.
- Logs can contain user text, model reasoning, tool arguments, and physical
  observations. Review deployment storage, access, and retention policies.

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.
All contributions must follow the repository's code-quality, testing, and
security requirements.

Thea is released under the [Apache License 2.0](LICENSE). Security issues
should be reported according to [SECURITY.md](SECURITY.md).

## Citation

If you find this work useful in your research, please cite:

```bibtex
@misc{thea2026,
  title  = {Towards the Harness of Embodied Agents},
  author = {Qi Wang and Tianyi Wang and Chengyang Li and Shikun Ban and Yurun Chen
            and Yizhong Ge and Jason Qin and Chengtai Li and Wentao Zhu},
  year   = {2026},
  note   = {Technical Report},
}
```
