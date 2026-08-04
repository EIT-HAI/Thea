# Thea Lark Channel

`thea-lark` connects Thea to Feishu or Lark and keeps one Harness session per
authorized user. It authenticates inbound events, renders progress and final
cards, sends named Observation views, and installs the model-visible
`query_user` and `notify_user` Tools.

The distribution name is `thea-lark`; Python code imports `thea_lark`.

## Install

From the repository root, the installer creates `.venv` and includes the
channel:

```bash
./install.sh
```

For a manual installation into the active Python environment:

```bash
python -m pip install './harness[anthropic]'
python -m pip install ./lark
cp lark/config.example.yaml config.yaml
```

Python 3.10 or newer is supported.

## Run the Channel

Create a Feishu or Lark app with its bot enabled and subscribe to
`im.message.receive_v1`. The [channel setup guide](docs/channel.md) lists the
required app capabilities. Add the application credentials, authorized sender
Open IDs, and the model credential selected by `config.yaml` to `.env`:

```text
LARK_APP_ID=cli_...
LARK_APP_SECRET=...
LARK_ALLOWED_OPEN_IDS=ou_trusted_user
ANTHROPIC_API_KEY=...
```

`LARK_ALLOWED_OPEN_IDS` is fail-closed. An unset or empty value authorizes no
parsed user message. Set `LARK_BOT_OPEN_ID` when the bot must identify group
mentions, and set `LARK_REGION=global` when using Lark rather than Feishu.

Validate configuration before opening a channel, then start the default
WebSocket transport:

```bash
./run.sh --mode channel --check
./run.sh --mode channel
```

These root launcher commands use the `.venv` created by `./install.sh`. After
a manual installation into another active environment, start the installed
entry point directly instead:

```bash
thea-lark --transport ws --config ./config.yaml
```

WebSocket transport needs outbound network access but no public callback URL.
Webhook deployments use `--transport webhook`, a TLS reverse proxy, and a
Verification Token or Encrypt Key; see the [channel setup guide](docs/channel.md).

## Connect Your Harness

Pass an importable `module:callable` factory when the channel should control a
simulator or physical robot:

```bash
./run.sh --mode robot --check \
  --harness-factory my_runtime.robot:create_harness

./run.sh --mode robot \
  --harness-factory my_runtime.robot:create_harness
```

The callable receives the validated Harness configuration and a
`ChannelSessionContext`, then returns one `Harness`. It runs once for each new
user session. The channel replaces the fallback `query_user` and `notify_user`
implementations with session-aware versions, so the deployment does not
reimplement those Tools. See [Channel setup and deployment](docs/channel.md)
for the factory signature and ownership rules.

## What the Channel Adds

| Capability | Behavior | Guide |
|---|---|---|
| User authorization | App-scoped sender Open IDs are checked before a message reaches the Harness. | [Configuration](docs/configuration.md) |
| Clarification | `query_user` pauses the active Agentic Loop for the same user's answer and can show candidate refs with current named views. | [Images and Named Views](docs/images.md) |
| Notification | `notify_user` sends a non-blocking progress, warning, or completion update. | [Channel setup](docs/channel.md) |
| Session lifecycle | Per-user sessions support cancellation, reset, Skill selection, bounded concurrency, and cleanup. | [Operations](docs/operations.md) |
| Rendering | Model progress, Tool activity, images, evaluation, and final text are rendered as Lark cards or plain text. | [Channel setup](docs/channel.md) |

## Documentation

Use the focused guide for the task at hand:

- [Channel setup and deployment](docs/channel.md)
- [Configuration reference](docs/configuration.md)
- [Images and named Observation views](docs/images.md)
- [Sessions, logs, shutdown, and operations](docs/operations.md)
- [Documentation website](https://eit-hai.github.io/thea/documentation/reference/lark.html)

## Security

Inbound authentication, sender authorization, group-mention checks, physical
Task serialization, image bounds, and SSRF controls fail closed by default.
Runtime YAML and factory paths are trusted operator inputs. Low-level robot
safety remains independent of this channel.

The package uses Apache License 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
