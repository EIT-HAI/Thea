# 安装与配置

## 环境要求

- Linux 或 macOS
- Python 3.10 或更新版本
- 模型 API key，除非部署侧注入自己的 `ModelProtocol`
- 只有启用飞书/Lark 通道时才需要 Lark 凭证
- 只有对应部署需要时，才需要单独安装仿真器或机器人 SDK

## 安装所有公开包

在仓库根目录运行：

```bash
./install.sh
```

安装脚本会创建 `.venv`，安装 Harness、仿真接口、模型适配器和 Lark 通道，并
初始化 `.env` 和 `config.yaml`。已有文件不会被覆盖。

如需指定 Python 解释器：

```bash
PYTHON=python3.12 ./install.sh
```

## 配置模型

在 `.env` 或进程环境中设置 `llm.api_key_env` 指向的凭证：

```text
ANTHROPIC_API_KEY=...
```

先在不联系模型服务商的情况下验证配置，再启动终端里的 Harness session：

```bash
./run.sh --mode cli --check
./run.sh --mode cli
```

终端模式使用配置中的真实模型，并沿用和其他部署一致的 Agentic Loop 与 context
生命周期。它不会启动 Lark，也不需要仿真器或真实机器人。使用
`--instruction "..."` 可以运行单个非交互式 Task。

根启动器默认读取 `.env` 和 `config.yaml`。所有模式都接受显式文件路径：

```bash
./run.sh --mode cli --env ./deployment.env --config ./deployment.yaml --check
```

初始 `config.yaml` 会选择模型并启用 bounded accumulated context compaction：

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

模型提供方、MCP servers、context compaction、Skills、Embodiment Profile、
Safety 和 Evaluation 的详细配置见英文
[Configuration](../reference/configuration.md)。

## 配置飞书/Lark 通道

把应用配置写入 `.env`：

```text
LARK_APP_ID=cli_...
LARK_APP_SECRET=...
LARK_ALLOWED_OPEN_IDS=ou_...
```

默认 WebSocket transport 不需要公网 callback URL。Webhook 部署还需要
verification token 或 encrypt key。

## 启动前验证 Lark 通道

```bash
./run.sh --mode channel --check
```

没有部署工厂函数时，启动检查会解析 YAML、检查 Lark 设置，并确认模型凭证或
mock replay 设置存在。它不会联系模型服务商，也不会启动 Harness session。

带部署工厂函数时，`simulation` 和 `robot` 的启动检查会检查 Lark 设置、导入
工厂函数，并确认指定属性可调用。Simulation 模式还会检查已安装的
`thea-simulation` 是否暴露预期公开接口。这些检查不会调用工厂函数，不会构造
仿真器或机器人资源，也不会验证工厂函数内部提供的模型和凭证。

## 只安装 Harness

不需要 Lark 或 simulation 的应用可以直接安装核心包：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install './harness[anthropic]'
cp harness/config.example.yaml config.yaml
thea-cli --config config.yaml
```

可选模型依赖见英文
[Models and Tools](../harness/model-and-tools.md)。

## 下一步

- 新机器人集成：阅读 [接入真实机器人](port-to-your-robot.md)。
- 已有场景表示：实现 [Scene Graph](scene-graph.md) 边界。
- 物理操作任务：接入 [Evaluation as Exit Codes](evaluation.md)。
