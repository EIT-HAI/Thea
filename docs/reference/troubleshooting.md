# Troubleshooting

| Symptom | Check |
|---|---|
| `Environment variable ... is not set` | Export the credential named by `llm.api_key_env`, or inject `ModelProtocol`. |
| Harness configuration not found | Run `./install.sh`, pass `--config`, or set `THEA_CONFIG`. |
| Embodiment Profile not found | Resolve the path below the working directory or `paths.base_dir`. |
| Mock provider requires `replay_path` | Set `llm.replay_path` or `LLM_REPLAY` to a JSONL decision file. |
| Simulation mode rejects startup | Configure Lark credentials, supply `--harness-factory module:callable`, and install `thea-simulation`. |
| MCP calls remain blocked after timeout | Stop physical execution, inspect the robot, and restart the affected runtime. |
| Evaluated Tool has no verdict | Confirm that the Tool returns a non-empty `run_id`, has a post-condition, and appears in `evaluation.required_tools`. |
| Scene Graph query rejects `obj` | Pass an object ref present in the latest Scene Graph Brief. |
| Tool protocol failure | Match the published `inputSchema` and return a documented success or failure envelope. |

## Inspect a run

`harness.observability.run_logger()` writes JSONL under:

1. `THEA_HARNESS_LOG_DIR`, when set;
2. `$XDG_STATE_HOME/thea-harness/runs`;
3. `~/.local/state/thea-harness/runs`.

Logs redact common credential fields and inline image data, but may still
contain user text, model reasoning, Tool arguments, and physical observations.
Treat them as sensitive deployment artifacts.

## Check the installation

```bash
./run.sh --mode cli --check
```

This validates the Harness configuration and real-model adapter without Lark
credentials or a provider request. Use `--mode channel --check` when validating
the Feishu/Lark integration as well.

For a custom simulator or robot deployment, include the same factory used by
the real run:

```bash
./run.sh --mode robot \
  --harness-factory my_runtime.robot:create_harness \
  --check
```
