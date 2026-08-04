# Install & Setup

## Requirements

- Linux or macOS
- Python 3.10 or newer
- A model API key, unless a deployment injects its own `ModelProtocol`
- Feishu/Lark credentials only when the channel is enabled
- A separately installed simulator or robot SDK only for that deployment

## Install all public packages

From the repository root:

```bash
./install.sh
```

The installer creates `.venv`, installs the Harness, simulation interfaces,
model adapters, and Lark channel, then initializes `.env` and `config.yaml`
without overwriting existing files.

Select another Python interpreter when needed:

```bash
PYTHON=python3.12 ./install.sh
```

## Configure the model

Set the credential named by `llm.api_key_env` in `.env` or in the process
environment:

```text
ANTHROPIC_API_KEY=...
```

Validate the model configuration without contacting the provider, then start
an interactive Harness session in the terminal:

```bash
./run.sh --mode cli --check
./run.sh --mode cli
```

Terminal mode uses the configured real model and the same Agentic Loop and
context lifecycle as other deployments. It does not start Lark and does not
require a simulator or physical robot. Use `--instruction "..."` for one
non-interactive Task.

The root launcher reads `.env` and `config.yaml` by default. All modes accept
explicit files when a deployment keeps its settings elsewhere:

```bash
./run.sh --mode cli --env ./deployment.env --config ./deployment.yaml --check
```

The installed `config.yaml` selects the model and enables bounded accumulated
context compaction. Its baseline is:

```yaml
servers: []

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
```

See [Configuration](../reference/configuration.md) for model providers, MCP
servers, context compaction, Skills, Embodiment Profile, safety, and
Evaluation settings.

## Configure the Lark channel

Add the required application values to `.env`:

```text
LARK_APP_ID=cli_...
LARK_APP_SECRET=...
LARK_ALLOWED_OPEN_IDS=ou_...
```

The default WebSocket transport does not require a public callback URL.
Webhook deployments additionally configure a verification token or encrypt
key.

## Validate the Lark channel before starting

```bash
./run.sh --mode channel --check
```

Without a deployment factory, this preflight parses the YAML, checks the Lark
settings, and verifies that the configured model credential or mock replay
setting is present. It does not contact the model provider or start a Harness
session.

With a deployment factory, the `simulation` and `robot` preflights check the
Lark settings, import the factory, and verify that the named attribute is
callable. Simulation mode also checks that the installed `thea-simulation`
package exposes its expected public interfaces. These factory preflights do
not call the factory, construct simulator or robot resources, or validate a
model and credentials supplied from inside the factory.

## Install only the Harness

Applications that do not need Lark or simulation can install the core package
directly:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install './harness[anthropic]'
cp harness/config.example.yaml config.yaml
thea-cli --config config.yaml
```

Available model extras are documented under
[Models and Tools](../harness/model-and-tools.md).

## Next step

- New robot integration: follow
  [Port to Your Robot](../harness/port-to-your-robot.md).
- Existing scene representation: implement the
  [Scene Graph boundary](../scene-graph/index.md).
- Physical manipulation: connect
  [Evaluation as Exit Codes](../evaluation/index.md).
