# Feishu/Lark

`thea-lark` connects Thea to Feishu or Lark and keeps one Harness session per
authorized user. It authenticates inbound events, authorizes senders, renders
progress and final cards, sends Observation images, and registers
`query_user` and `notify_user`.

## Install

The root installer includes the channel:

```bash
./install.sh
```

For a manual installation:

```bash
python -m pip install './harness[anthropic]'
python -m pip install ./lark
```

## Configure

```bash
LARK_APP_ID=cli_...
LARK_APP_SECRET=...
LARK_ALLOWED_OPEN_IDS=ou_trusted_user
ANTHROPIC_API_KEY=...
```

`LARK_ALLOWED_OPEN_IDS` is fail-closed. An unset or empty value authorizes no
parsed user message. Set `LARK_BOT_OPEN_ID` when the bot must identify group
mentions. Set `LARK_REGION=global` for Lark; the default `cn` uses Feishu
endpoints.

## Start

The default WebSocket transport does not require a public callback URL:

```bash
./run.sh --mode channel --check
./run.sh --mode channel
```

A simulator or robot deployment supplies a channel composition factory:

```bash
./run.sh --mode robot \
  --harness-factory my_runtime.robot:create_harness
```

The factory receives trusted configuration and a `ChannelSessionContext`, then
returns one `Harness`.

## User interaction Tools

`query_user` pauses the active Agentic Loop until the same authorized user
answers, cancels, or the timeout expires:

```json
{
  "question": "Which bottle do you mean?",
  "candidate_refs": ["bottle_12", "bottle_18"],
  "observation_views": ["front", "wrist"]
}
```

Candidate refs are shown as structured choices. Named views resolve only from
the latest Observation.

`notify_user` sends a one-way `progress`, `warning`, or `completion` update and
does not wait for a reply.

## Operational boundary

Physical Tasks are serialized across user sessions by default. Increase
`LARK_DEPLOYMENT_MAX_CONCURRENCY` only when the deployment owns genuinely
independent physical resources.

The channel provides authentication, sender authorization, interaction
delivery, and session routing. Low-level robot safety remains independent of
the channel.
