# Models and Tools

## Built-in model adapters

When `model=` is omitted, `Harness` builds a provider from `config.llm`:

```python
from harness import Harness

harness = Harness(
    {
        "servers": [],
        "llm": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
            "max_tokens": 4096,
        },
    }
)
```

| Provider | Extra | Credential |
|---|---|---|
| `anthropic` | `harness[anthropic]` | `ANTHROPIC_API_KEY` |
| `openai` | `harness[openai]` | `OPENAI_API_KEY` |
| `openrouter` | none | `OPENROUTER_API_KEY` |
| `mimo` | none | `MIMO_API_KEY` |
| `qwen` | `harness[openai]` | `DASHSCOPE_API_KEY` |
| `deepseek` | `harness[openai]` | `DEEPSEEK_API_KEY` |
| `ollama` | `harness[openai]` | no remote credential |
| `mock` | none | `llm.replay_path` or `LLM_REPLAY` |

Provider credentials belong in environment variables or a secret manager, not
committed YAML.

## Custom model adapter

Implement `ModelProtocol` when a deployment needs another provider:

```python
from harness import Context, ModelResponse


class MyModel:
    def call(
        self,
        context: Context,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        # Translate context.provider_system_content,
        # context.messages_for_model(), and tools to one provider request.
        # Translate the provider response back to ModelResponse.
        ...
```

The adapter translates one request and response and owns its provider request
and retry behavior. The built-in adapters include bounded provider retries.
The Harness owns context history, task control, Tool execution, and recovery
from returned failures.

## Callable tools

`ToolRegistry.tool()` derives a JSON Schema from an annotated signature:

```python
from harness import ToolRegistry

registry = ToolRegistry()


@registry.tool(description="Navigate to one confirmed Scene Graph ref.")
def navigate_to(target: str) -> dict:
    run_id = start_navigation(target)
    return {"success": True, "run_id": run_id}
```

Use `BuiltinTool` when the schema or post-condition must be explicit:

```python
from harness import BuiltinTool

registry.register(
    BuiltinTool(
        name="notify_user",
        description="Send a one-way progress notification.",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "minLength": 1},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        fn=lambda message: {
            "success": True,
            "observation": send_notification(message),
        },
    )
)
```

## MCP tools

Trusted stdio MCP capability processes can expose navigation, manipulation,
or external services. Give each heavyweight backend capability its own
process; that process may expose several related Tool endpoints:

```yaml
servers:
  - name: robot-tools
    command: python
    args: ["-m", "robot_runtime.mcp_server"]
    call_timeout_sec: 300
    timeout_argument_margin_sec: 60
```

The Harness starts configured servers, discovers their tools, and exposes one
flat model-visible list. MCP `command`, `args`, `cwd`, and `env` are trusted
process-launch configuration and must never come from a model or remote user.

After a timeout, the MCP client blocks subsequent calls because the physical
execution state may be unknown. Inspect and restart the affected runtime
before issuing another physical action.

## Tool Definition

The model sees:

```json
{
  "name": "pick_object",
  "description": "When and how to use the tool.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "target": {"type": "string"}
    },
    "required": ["target"],
    "additionalProperties": false
  }
}
```

Tool Experience may be appended to `description` at task start. Evaluator
post-conditions remain hidden.

## Tool Result

Every tool returns one success or failure envelope:

```python
{"success": True, "observation": "the drawer opened"}
{"success": True, "run_id": "run-123"}
{"success": False, "reason": "the target is not visible"}
```

A successful result requires at least one value field beyond `success`. A
failed result requires a non-empty `reason`. Argument and result validation
failures use the same failure envelope so the model can recover inside the
Agentic Loop.

An evaluated physical tool must return a non-empty `run_id`.
