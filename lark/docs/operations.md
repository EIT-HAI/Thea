# Lark Operations

## Transport selection

Use WebSocket transport when the host can make outbound connections to
Feishu/Lark. It requires no public callback:

```bash
thea-lark \
  --transport ws \
  --host 127.0.0.1 \
  --port 8765 \
  --config ./config.yaml
```

Use webhook transport when deployment policy requires inbound callbacks:

```bash
thea-lark \
  --transport webhook \
  --host 127.0.0.1 \
  --port 8765 \
  --config ./config.yaml
```

Keep the process on loopback behind a TLS reverse proxy. Forward only
`/webhook/lark`. If the proxy is in another host or network namespace, bind to
the necessary private interface rather than exposing the port broadly.

Both modes expose:

```bash
curl --fail http://127.0.0.1:8765/health
```

## Sessions

Sessions are keyed by sender Open ID and reused for six idle hours by default.
`LARK_SESSION_TTL_SEC` changes the idle window; values below 60 seconds are
raised to 60. Expired sessions are closed before replacement.
`LARK_MAX_SESSIONS` bounds process-local sessions and defaults to 64.

Replies to a pending `query_user` resolve before session locks and the
deployment-wide task semaphore are acquired. The default single-task guard
therefore does not prevent an answer from reaching a waiting task.

## Physical-task serialization

The safe default is one active physical task across every user session:

```bash
export LARK_DEPLOYMENT_MAX_CONCURRENCY=1
```

Increase the value only when the deployment has independent robot resources
and every provider is safe for concurrent use. Per-user locks continue to
serialize messages inside one session.

The guard is process-local, not distributed. Run one channel process per
physical robot or add deployment-level coordination before using replicas.

## Commands

| Command | Effect |
|---|---|
| `/status` | Show model, accumulated-message count, idle time, and provider token usage when available. |
| `/reset` or `/clear` | Cancel cooperatively, close the current Harness, and remove the session. |
| `/cancel` | Cancel a pending query or request cancellation after the current blocking tool. |
| `/help` | List commands and registered tools. |
| `/skill` | List Skills, load one for the next task, or run a task with one Skill. |
| `/menu` | Enter the deterministic menu interface. |

Slash commands for physical tools still pass through the Agentic Loop, schema
validation, and hooks. They do not directly invoke robot methods.

## Logs

Each task writes a JSONL event log below:

```text
$XDG_STATE_HOME/thea-lark/runs/
```

When `XDG_STATE_HOME` is unset or relative, the fallback is:

```text
~/.local/state/thea-lark/runs/
```

Override the directory with:

```bash
export LARK_RUN_LOG_DIR=./runtime/lark-runs
```

Logs use owner-only permissions, redact common secret fields, and bound
oversized events. They can still contain user instructions, model traces, tool
arguments, and physical observations.

## Shutdown

Use `SIGTERM` for normal production shutdown. The ASGI lifespan closes every
Harness session, its owned resources, the Lark HTTP client, and the server
event loop. Avoid `SIGKILL` because it bypasses Harness cleanup.

Cooperative cancellation cannot interrupt a physical tool that is already
blocking. The deployment must provide its own interrupt and emergency-stop
path.

## Troubleshooting

| Symptom | Check |
|---|---|
| Messages are ignored | Confirm `LARK_ALLOWED_OPEN_IDS`, app-scoped Open IDs, and group mentions. |
| Startup reports missing credentials | Export both `LARK_APP_ID` and `LARK_APP_SECRET`. |
| Webhook returns `503` | Configure a Verification Token or Encrypt Key and verify console delivery settings. |
| Factory import fails | Put the deployment package on `PYTHONPATH` and use exact `module:callable` syntax. |
| A requested image is unavailable | Publish the named view in the latest Observation. |
| A local path is rejected | Set an allowed image root and keep the resolved file inside it. |
