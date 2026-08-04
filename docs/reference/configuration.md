# Configuration

The Harness accepts a trusted mapping, normally loaded from `config.yaml`.
Deployment paths, process launch settings, and credentials must not come from
the model or an untrusted remote user.

## Baseline

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

## Model

`llm` is used only when the application does not inject `model=` into
`Harness`.

| Key | Meaning |
|---|---|
| `provider` | `anthropic`, `openai`, `openrouter`, `mimo`, `qwen`, `deepseek`, `ollama`, or the test-only `mock` replay provider |
| `model` | Provider model identifier; omitting it uses that adapter's built-in or environment-selected default |
| `api_key_env` | Preferred name of the environment variable containing the credential |
| `api_key` | Direct credential accepted for compatibility; avoid it in YAML and source control |
| `base_url` | Optional OpenAI-compatible endpoint override; not used by `anthropic` or `mock` |
| `max_tokens` | Maximum generated tokens when supported by the selected adapter |
| `reasoning_effort` | Optional reasoning setting for the built-in `openai` adapter |
| `service_tier` | Optional service-tier setting for the built-in `openai` adapter |
| `collapse_tool_history` | Collapse completed Tool Call history for the built-in `openai` adapter when truthy |
| `replay_path` | JSONL decision source required by the `mock` provider unless `LLM_REPLAY` is set |

The public YAML surface does not currently pass `temperature`, request
timeouts, or retry counts into `ModelClient`. Built-in adapters use their
code-level defaults. A deployment that needs different request or retry
behavior should inject its own `ModelProtocol` or construct a provider adapter
directly.

See [Models and Tools](../harness/model-and-tools.md) for provider extras and
credentials.

## Accumulated-context compaction

Compaction changes only the Accumulated lifetime. Resident and Refreshed
context remain outside the summarization request.

| Key | Default | Meaning |
|---|---:|---|
| `enabled` | `true`, except for `mock` | Enable checkpoint generation near the context limit |
| `context_window` | `200000` | Estimated total context capacity in tokens |
| `reserve_tokens` | `16384` | Capacity reserved for the next model response; must be smaller than `context_window` |
| `keep_recent_turns` | `4` | Maximum number of recent complete model turns considered for verbatim retention |
| `keep_recent_tokens` | `20000` | Estimated-token ceiling for the retained recent tail |
| `tool_result_max_chars` | `2000` | Per-result character bound in a compaction request |
| `reasoning_max_chars` | `2000` | Per-message reasoning bound in a compaction request |
| `max_input_chars` | `400000` | Maximum serialized characters supplied to one summary round; effective minimum `10000` |
| `max_summary_rounds` | `8` | Maximum ordered summary rounds for one compaction attempt |

A failed or malformed checkpoint leaves the original Accumulated messages in
place. `compaction_model=` can supply a dedicated `ModelProtocol`; otherwise
the Harness uses its main model.

## MCP servers

Each `servers` item defines one trusted stdio MCP capability process:

```yaml
servers:
  - name: robot-tools
    command: python
    args: ["-m", "robot_runtime.mcp_server"]
    cwd: ./deployment
    env:
      ROBOT_PROFILE: lab
    transport: stdio
    call_timeout_sec: 300
    timeout_argument_margin_sec: 60
```

| Key | Required | Meaning |
|---|---:|---|
| `name` | no | Process identifier used in diagnostics; defaults to `unnamed` |
| `command` | yes | Executable launched by the Harness |
| `args` | no | List of string command arguments |
| `cwd` | no | Working directory for the subprocess |
| `env` | no | Additional environment mapping with scalar values |
| `transport` | no | Must be `stdio`; it is the only supported public transport |
| `call_timeout_sec` | no | Positive default timeout for a Tool Call |
| `timeout_argument_margin_sec` | no | Non-negative margin added when a Tool argument declares a longer timeout |

The Harness starts every configured process, discovers its Tool Definitions,
and exposes one flat model-visible Tool list. Duplicate Tool names are
rejected. A timeout leaves physical state uncertain and blocks later MCP calls
until the runtime is restarted.

## Evaluation

```yaml
evaluation:
  required_tools: [pick_object, place_object]
  segment_tools: []
  max_segments: 8
  post_conditions:
    pick_object: >-
      The requested object is visibly lifted and held by the gripper.
    place_object: >-
      The requested object is visibly supported by the target surface.
```

| Key | Meaning |
|---|---|
| `required_tools` | Tools whose completed executions structurally trigger hidden `evaluate_run` |
| `segment_tools` | Evaluated Tools allowed to return `process`; must be a subset of `required_tools` |
| `max_segments` | Positive action-segment budget for segmented Tools |
| `post_conditions` | Non-empty Tool-name to post-condition mapping used for MCP Tools |

An in-process `BuiltinTool` can carry its `post_condition` directly. Every
evaluated Tool must return a non-empty `run_id`, and the deployment must supply
both an evaluator and matching post-execution evidence.

## Safety

`safety.base_clearance_margin_m` is a finite, non-negative distance in metres.
The default `BaseMotionSafetyFilter` subtracts it from fresh directional
clearance before admitting or clamping supported base translations.

## Deployment paths, Skills, and the Embodiment Profile

```yaml
paths:
  base_dir: ./deployment

skills:
  dir: ./skills

embodiment_profile_file: ./profiles/robot.md
```

Relative Skill and Embodiment Profile paths resolve first against
`paths.base_dir`, when present, and then against the process working directory.
Package and source-checkout locations are not inferred. The Scene Graph,
Memory implementation, Observation, evaluator, and robot resources are Python
dependencies supplied to `Harness`; the public YAML does not construct those
backends.

## Process overrides

These variables affect the repository launchers or Harness runtime:

| Variable | Scope | Meaning |
|---|---|---|
| `THEA_CONFIG` | `run.sh`, terminal CLI | Trusted YAML path used when `--config` is absent |
| `THEA_ENV` | `run.sh`, Lark | Dotenv path used when `--env` is absent |
| `THEA_VENV` | `install.sh`, `run.sh` | Virtual-environment directory |
| `LLM_PROVIDER` | model selection | Provider override when an explicit command option is absent |
| `LLM_REPLAY` | `mock` provider | JSONL replay path when `llm.replay_path` is absent |
| `THEA_MCP_CALL_TIMEOUT_SEC` | MCP | Default call timeout when a server item omits `call_timeout_sec` |
| `THEA_MCP_TIMEOUT_ARGUMENT_MARGIN_SEC` | MCP | Timeout-argument margin when a server item omits it |
| `THEA_HARNESS_LOG_DIR` | Harness logging | Explicit JSONL run-log directory |
| `XDG_STATE_HOME` | Harness and Lark logging | Base state directory when an explicit log directory is absent |

Feishu/Lark-specific variables are summarized in the
[Lark reference](lark.md) and documented in full in
[`lark/docs/configuration.md`](https://github.com/EIT-HAI/Thea/blob/main/lark/docs/configuration.md).

## Validate programmatically

```python
from harness import validate_runtime_config

config = validate_runtime_config(raw_config)
```

This validates Harness-owned section shapes and invariants. Constructing the
selected model, MCP processes, and deployment providers performs the remaining
dependency-specific checks.
