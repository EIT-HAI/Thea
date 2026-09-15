# 使用方式

Thea 是一个面向具身智能体的 Harness。它负责 Agentic Loop、上下文生命周期、
Tool 执行、执行后评估，以及部署侧向模型提供物理世界证据的边界。

公开版本可以通过五种方式使用：

| 部署方式 | 你需要提供 | Thea 提供 |
|---|---|---|
| 本地终端 | 配好的模型凭证 | 完整 Agentic Loop 和终端交互，不需要 Lark 或机器人 |
| 飞书/Lark 通道 | 应用凭证、授权用户和模型配置 | 按用户隔离的 Harness 会话、卡片、图片、澄清和通知 |
| Harness library | 一个模型和模型可见的 Tools | Agentic Loop、Context、Tool Protocol、Memory、Skills、hooks 和日志 |
| 仿真器 | LIBERO 或 RoboTwin 2.0 环境，以及基础策略 | Observation 投影、由策略支撑的 Tool 集成和评估器接入 |
| 真实机器人 | 机器人 SDK 适配器、感知、策略、Scene Graph 和执行后证据 | 与仿真相同的运行时和公开接口 |

根启动脚本把这些路径映射到四种命令模式：

| 命令模式 | 启动的进程 | 额外要求 |
|---|---|---|
| `--mode cli` | 交互式终端，或带 `--instruction` 的单条指令 | 模型配置和凭证 |
| `--mode channel` | 使用默认 Harness 的飞书/Lark 通道 | 模型和 Lark 凭证 |
| `--mode simulation` | 使用仿真 Harness 工厂函数的飞书/Lark 通道 | Lark 凭证、可导入的工厂函数和 `thea-simulation` |
| `--mode robot` | 使用机器人 Harness 工厂函数的飞书/Lark 通道 | Lark 凭证和可导入的工厂函数 |

`simulation` 和 `robot` 是由通道托管的部署模式，不是独立的仿真器或硬件
可执行程序。不使用 Lark 的应用可以在 Python 中直接组合 `Harness` 并调用
`run_stream()`。完整启动参数可运行：

```bash
./run.sh --help
```

## 选择起点

### 在终端中运行 Harness

安装仓库，配置 `.env` 和 `config.yaml` 中的真实模型，然后启动交互式终端：

```bash
./install.sh
./run.sh --mode cli --check
./run.sh --mode cli
```

这条路径不需要 Lark 凭证，不需要仿真器，也不需要真实机器人。它也支持执行
单条指令：

```bash
./run.sh --mode cli --instruction \
  "Notify the user that the Harness is ready, then explain what ran."
```

### 启动完整通道

安装公开包，配置模型和飞书/Lark 凭证，然后启动通道：

```bash
./install.sh
./run.sh --mode channel --check
./run.sh --mode channel
```

通道会为每个授权用户创建一个 Harness 会话，并把 `query_user` 和
`notify_user` 注册为模型可见的 Tools。

### 连接仿真器

单独安装目标 benchmark 环境，构造一个返回 `Harness` 的工厂函数，配置 Lark
凭证，并在启动通道前运行启动检查：

```bash
./run.sh --mode simulation --check \
  --harness-factory my_runtime.sim:create_harness

./run.sh --mode simulation \
  --harness-factory my_runtime.sim:create_harness
```

工厂函数负责连接 benchmark episode、基础策略 Tools、Observation provider 和
评估器。LIBERO 与 RoboTwin 2.0 适配边界见英文
[Simulation](../reference/simulation.md)。

### 连接真实机器人

硬件 SDK 和部署专用代码应放在 Harness package 外部。部署包通过公开接口提供
工厂函数、Tools、Observation、Scene Graph、Evaluation 和资源归属：

```bash
./run.sh --mode robot --check \
  --harness-factory my_runtime.robot:create_harness

./run.sh --mode robot \
  --harness-factory my_runtime.robot:create_harness
```

从 [接入真实机器人](port-to-your-robot.md) 开始，然后在物理执行准备好后接入
[Scene Graph](scene-graph.md) 和
[Evaluation as Exit Codes](evaluation.md)。

## 一个 task 如何运行

一条用户指令启动一个 Task。每个 Turn 都遵循同一个反应式循环：

```text
refresh evidence
  -> model decision
  -> execute at most one selected Tool
  -> run configured hooks
  -> record the result
  -> refresh again
```

模型可以同时返回文本和一个 Tool Call。Task 在模型不再返回 Tool Call，或
Harness 终止运行时结束。最终文本就是任务报告。

## 推荐阅读顺序

1. [安装与配置](setup.md)
2. [Harness 核心机制](harness.md)
3. [接入真实机器人](port-to-your-robot.md)
4. [Scene Graph](scene-graph.md)
5. [Evaluation as Exit Codes](evaluation.md)
6. [参考资料](reference.md)
