# Lark Configuration

## Harness configuration

The channel loads one trusted YAML mapping in this order:

1. `--config ./path/to/config.yaml`;
2. `LARK_HARNESS_CONFIG`;
3. the packaged `config.example.yaml`.

The packaged example contains no secret and reads the model credential from
the environment. `--provider` or `LLM_PROVIDER` can override its provider. A
provider change removes stale provider-specific fields before constructing the
Harness.

Harness YAML is trusted deployment configuration. MCP entries can launch local
processes; never derive `command`, `args`, `cwd`, or `env` from a user message.

## Harness factory

Set `LARK_HARNESS_FACTORY` or pass `--harness-factory` using
`module:callable` syntax. The callable receives:

```python
def create_harness(
    config: dict[str, Any],
    session_context: ChannelSessionContext,
) -> Harness: ...
```

`ChannelSessionContext` is a frozen public record containing:

- `user_id`, the authorized sender's app-scoped Open ID;
- `chat_id`, the conversation that created the session;
- `transport`, either `webhook` or `ws`.

The factory runs once when a user session is created. Pass robot, camera, and
transport resources that should close with the session through
`owned_resources=` when constructing `Harness`.

## Environment reference

| Variable | Default | Purpose |
|---|---:|---|
| `THEA_ENV` | `./.env` | Dotenv file loaded by the channel; the root `--env` option sets this automatically. |
| `LARK_APP_ID` | required | Feishu/Lark application ID. |
| `LARK_APP_SECRET` | required | Application secret. |
| `LARK_ALLOWED_OPEN_IDS` | deny all | Authorized sender Open IDs; `*` is explicit allow-all. |
| `LARK_BOT_OPEN_ID` | empty | Bot Open ID used to identify group mentions. |
| `LARK_REGION` | `cn` | Set `global` for Lark endpoints. |
| `LARK_VERIFICATION_TOKEN` | empty | Webhook payload trust anchor. |
| `LARK_ENCRYPT_KEY` | empty | Webhook signature and decryption key. |
| `LARK_WEBHOOK_MAX_SKEW_SEC` | `300` | Maximum signed-webhook timestamp skew. |
| `LARK_HARNESS_CONFIG` | packaged example | Trusted Harness YAML path. |
| `LARK_HARNESS_FACTORY` | none | Deployment factory in `module:callable` form. |
| `LLM_PROVIDER` | YAML value | Optional model-provider override. |
| `LARK_DEPLOYMENT_MAX_CONCURRENCY` | `1` | Process-wide Agentic Loop limit. |
| `LARK_MAX_SESSIONS` | `64` | Process-local session limit. |
| `LARK_SESSION_TTL_SEC` | `21600` | Session idle lifetime in seconds, minimum 60. |
| `LARK_QUERY_USER_TIMEOUT_SEC` | `300` | Wait for a user answer, minimum 5 seconds. |
| `LARK_QUERY_USER_MAX_IMAGES` | `8` | Images attached to one query or final card, clamped to 1-20. |
| `LARK_RENDERING_MODE` | `auto` | Use `raw` for plain-text output. |
| `LARK_RUN_LOG_DIR` | XDG state path | JSONL run-log directory. |
| `LARK_IMAGE_ALLOWED_ROOT` | disabled | Allowed root for trusted local image paths. |
| `LARK_ALLOW_REMOTE_IMAGES` | disabled | Permit validated public HTTPS images. |
| `LARK_IMAGE_ALLOWED_HOSTS` | empty | Exact, comma-separated HTTPS hosts allowed when remote images are enabled. |
| `LARK_IMAGE_MAX_BYTES` | `10485760` | Maximum encoded bytes per image. |
| `LARK_IMAGE_MAX_PIXELS` | `20000000` | Maximum pixels per NPY image. |
| `LARK_HTTP_TIMEOUT_SEC` | `15` | Lark API request timeout. |
| `LARK_HTTP_RETRIES` | `3` | Retry count for retryable API errors. |
| `LARK_HTTP_CONNECT_RETRIES` | `2` | Connection retries. |
| `LARK_HTTP_PROXY` | standard proxy environment | Explicit proxy for Lark API traffic. |

## Authorization

`LARK_ALLOWED_OPEN_IDS` accepts a comma-separated list. Whitespace is ignored.
Open IDs are app-scoped, so obtain them from a trusted tenant directory or
authenticated event inspection. The value is fail-closed: missing or empty
authorizes no parsed user message.

Group messages must mention the bot. `LARK_BOT_OPEN_ID` lets the adapter
identify and remove the mention token before passing the instruction to the
Harness.

## Webhook trust

Webhook mode requires a Verification Token, Encrypt Key, or both. Signed
timestamps outside `LARK_WEBHOOK_MAX_SKEW_SEC` are rejected. When encryption
is configured, signature fields and encrypted payloads are mandatory.

WebSocket mode is authenticated with the App ID and App Secret. Keep the token
and Encrypt Key configured when the same app also uses those protections.
