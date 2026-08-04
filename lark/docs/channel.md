# Thea Lark Channel

Thea Lark Channel connects the provider-neutral Thea Harness to Feishu or
Lark. It authenticates inbound events, authorizes senders, keeps one Harness
session per user, renders progress and final cards, sends images, and provides
live `query_user` and `notify_user` tools.

The distribution is `thea-lark`; the import package is `thea_lark`.
The different import name avoids a collision with the unrelated `lark` parser
package.

## Requirements

- Python 3.10 or newer;
- a custom Feishu or Lark app with its bot enabled;
- outbound access for WebSocket transport, or public HTTPS ingress for
  webhook transport;
- a trusted local Harness configuration.

## Install from source

From the repository root, install the Harness, simulation interfaces, and
channel. The installer initializes `.env` and `config.yaml` without
overwriting existing files. After filling those files, start the channel:

```bash
./install.sh
# Fill in .env and review config.yaml.
./run.sh --mode channel
```

`run.sh` defaults to WebSocket transport and honors
`LARK_HARNESS_FACTORY`. Simulation and physical-robot deployments select
`--mode simulation` or `--mode robot` and provide a Harness factory. Use
`./run.sh --mode channel --check` for a non-network preflight. For a manual
installation:

```bash
python -m pip install './harness[anthropic]'
python -m pip install ./lark
```

## Quick start with WebSocket transport

### 1. Configure the app

Create a custom app in the
[Feishu Open Platform](https://open.feishu.cn/app) or
[Lark Developer Console](https://open.larksuite.com/app), then:

1. enable the bot;
2. subscribe to `im.message.receive_v1`;
3. select WebSocket or long-connection event delivery;
4. grant only the permissions needed to receive direct messages, receive
   group `@bot` messages, send as the bot, and access message images;
5. publish the app to the intended tenant and add the bot to target groups.

Current consoles commonly expose these scope identifiers:

```text
im:message.p2p_msg:readonly
im:message.group_at_msg:readonly
im:message:send_as_bot
im:resource
```

Scope labels and bundling can differ between Feishu and Lark releases. Verify
the four capabilities rather than adding unrelated permissions.

### 2. Configure credentials and authorization

```bash
export LARK_APP_ID=cli_...
export LARK_APP_SECRET=...
export LARK_ALLOWED_OPEN_IDS=ou_trusted_user
export ANTHROPIC_API_KEY=...
```

`LARK_ALLOWED_OPEN_IDS` is fail-closed. An unset or empty value authorizes no
parsed user message. Use `*` only for a deliberately open test deployment.
Set `LARK_BOT_OPEN_ID` when the bot must identify group mentions. Set
`LARK_REGION=global` for Lark; the default `cn` uses Feishu endpoints.

### 3. Start the channel

```bash
thea-lark \
  --transport ws \
  --config ./lark/config.example.yaml
```

Check the loopback health listener:

```bash
curl --fail http://127.0.0.1:8765/health
```

The ready response is:

```json
{"ok": true, "channel": "lark"}
```

## Connect a robot deployment

Without a factory, the channel creates `Harness(config)` for provider and
channel testing. A physical deployment supplies a synchronous factory in
`module:callable` form:

```python
# robot_runtime/channel.py
from typing import Any

from harness import Harness, ToolRegistry
from thea_lark import ChannelSessionContext


def create_harness(
    config: dict[str, Any],
    session_context: ChannelSessionContext,
) -> Harness:
    robot = connect_robot(session_context)
    registry = ToolRegistry()
    register_robot_tools(registry, robot)

    return Harness(
        config,
        model=build_model(config),
        registry=registry,
        observation_provider=build_observation_provider(robot),
        base_clearance_provider=build_base_clearance_provider(robot),
        scene_graph=build_scene_graph(robot),
        memory=build_memory(session_context),
        evaluator=build_evaluator(robot),
        post_execution_observation_provider=build_post_execution_observer(robot),
        owned_resources=(robot,),
    )
```

Select it without changing either open package:

```bash
export LARK_HARNESS_FACTORY=robot_runtime.channel:create_harness
thea-lark --transport ws --config ./config.yaml
```

`ChannelSessionContext` provides the authorized `user_id`, originating
`chat_id`, and selected transport. The factory runs once per new user session
and must return `Harness`.

The runtime installs session-aware `query_user` and `notify_user`
implementations. A deployment does not need to implement those channel tools.

## User interaction

`query_user` pauses the current Agentic Loop until the same authorized user
answers, sends `/cancel`, or the timeout expires:

```json
{
  "question": "Which bottle do you mean?",
  "candidate_refs": ["bottle_12", "bottle_18"],
  "observation_views": ["front", "wrist"]
}
```

Candidate refs are shown as structured choices in the question. Named views
resolve only from the latest Observation. A missing view returns a Tool Result
failure rather than attaching stale evidence.

`notify_user` sends a one-way `progress`, `warning`, or `completion` update and
never waits for a reply. Use `query_user` when the task requires an answer.

## Webhook transport

For callback delivery, configure at least one trust anchor:

```bash
export LARK_VERIFICATION_TOKEN=...
export LARK_ENCRYPT_KEY=...  # when event encryption is enabled

thea-lark \
  --transport webhook \
  --host 127.0.0.1 \
  --port 8765 \
  --config ./config.yaml
```

Expose only `/webhook/lark` through a TLS reverse proxy. Webhook handling fails
closed when no trust anchor is configured. When an Encrypt Key is present,
signature fields and encrypted bodies are verified before dispatch.

## Sessions and commands

Physical tasks are serialized across all user sessions by default:

```bash
export LARK_DEPLOYMENT_MAX_CONCURRENCY=1
```

Increase this value only for genuinely independent robot resources. The guard
is process-local; multiple replicas need deployment-level coordination.

| Command | Effect |
|---|---|
| `/status` | Show session, model, message, and available token-usage information. |
| `/reset` or `/clear` | Cancel cooperatively, close the Harness, and remove the session. |
| `/cancel` | Cancel a pending query or request task cancellation after the current tool. |
| `/help` | List channel commands and registered tools. |
| `/skill` | List Skills or load one for the next task. |
| `/menu` | Enter the deterministic menu interface. |

## Detailed guides

- [Configuration reference](configuration.md)
- [Images and named views](images.md)
- [Sessions, logs, shutdown, and operations](operations.md)

## Troubleshooting

| Symptom | Check |
|---|---|
| Messages are ignored | Confirm the allowlist, app-scoped sender Open ID, and group bot mention. |
| Missing app credential | Export both `LARK_APP_ID` and `LARK_APP_SECRET`. |
| Webhook returns `503` | Configure a Verification Token or Encrypt Key. |
| Factory import fails | Put the deployment package on `PYTHONPATH` and use exact `module:callable` syntax. |
| Requested image is unavailable | Publish the named view in the latest Observation. |

## Security

Inbound authentication, sender authorization, group mention checks, physical
task serialization, image bounds, and SSRF controls fail closed by default.
Runtime YAML and factory paths are trusted operator inputs. Low-level robot
safety remains independent of this channel.

The package uses Apache License 2.0. See [LICENSE](../LICENSE) and
[NOTICE](../NOTICE).
