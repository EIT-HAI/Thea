# Robot Port Template

Copy this directory to an importable deployment package, rename it (for
example, `my_robot`), and replace the explicit `NotImplementedError` boundaries
in `factory.py` with your SDK, model, sensing, Scene Graph, and evaluator
adapters.

The three integration files have separate responsibilities:

| File | Replace with |
|---|---|
| `profile.md` | Stable properties of the active embodiment. |
| `tools.py` | Model-visible navigation and manipulation implementations. |
| `factory.py` | Model, Observation, clearance, Scene Graph, Evaluation, and owned resources. |

Point `config.yaml` at the copied profile:

```yaml
embodiment_profile_file: my_robot/profile.md
```

From the repository root after running `./install.sh`, validate the import path
and configuration without connecting robot resources:

```bash
./run.sh --mode robot --check \
  --harness-factory my_robot.factory:create_harness
```

Then launch the package through the same factory:

```bash
./run.sh --mode robot \
  --harness-factory my_robot.factory:create_harness
```

These root `robot` commands host the factory through Feishu/Lark and require
the channel credentials in `.env`. A non-Lark application can import the same
deployment components, construct `Harness`, and call `run_stream()` directly.

See the [Port to Your Robot](../../docs/harness/port-to-your-robot.md) guide
before connecting physical execution. Harness checks do not replace emergency
stops, actuator limits, collision avoidance, or low-level motion control.
