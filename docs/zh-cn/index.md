# Thea 中文文档

Thea 是一个面向具身智能体的 Harness。它把模型、物理世界工具、当前观测、
持久场景状态、执行后评估和可复用经验放进同一个任务循环里，同时不绑定某个
模型供应商、机器人 SDK、仿真器、策略后端或用户通道。

这份中文文档覆盖 Thea 的核心概念、安装方式、部署路径和安全边界。
GitHub 上也提供
[中文 README](https://github.com/EIT-HAI/Thea/blob/main/README.zh-CN.md)。

## 文档目录

```{toctree}
:maxdepth: 2

usage
setup
harness
port-to-your-robot
scene-graph
evaluation
reference
```

## 一分钟快速开始

本地终端模式只需要模型配置，不需要 Feishu/Lark、仿真器或真实机器人：

```bash
git clone https://github.com/EIT-HAI/Thea.git
cd Thea
./install.sh
./run.sh --mode cli --check
./run.sh --mode cli
```

如需启动 Feishu/Lark 通道、连接仿真器，或接入真实机器人，请先完成模型与
通道凭证配置，再按照对应部署模式运行 `./run.sh`。

## 从哪里开始读

| 目标 | 推荐入口 |
|---|---|
| 先跑通 Thea | [使用方式](usage.md) |
| 安装、模型密钥和 Lark 配置 | [安装与配置](setup.md) |
| 理解 Agentic Loop、Context 和 Tool Protocol | [Harness 核心机制](harness.md) |
| 接入自己的机器人 | [接入真实机器人](port-to-your-robot.md) |
| 接入可查询的世界状态 | [Scene Graph](scene-graph.md) |
| 用执行后证据判断任务是否完成 | [Evaluation as Exit Codes](evaluation.md) |
| 查配置、API、仿真和 Lark 细节 | [参考资料](reference.md) |

## Thea 提供什么

- `Harness` 负责任务循环、上下文生命周期、工具调用、执行后评估和日志。
- 部署侧负责模型凭证、机器人或仿真器连接、感知、策略、Scene Graph 后端和
  安全系统。
- `Tool` 暴露模型可以选择的动作或查询能力，Harness 每轮最多执行一个被选中
  的 Tool Call。
- `Observation` 和 `Scene Graph` 为每次决策提供当前证据和可引用的世界状态。
- `Evaluation` 在动作执行后根据物理证据返回退出码，而不是只依赖模型自述。

## 部署方式

| 模式 | 适合场景 |
|---|---|
| `--mode cli` | 在终端中验证 Harness、模型配置和基本工具调用。 |
| `--mode channel` | 通过 Feishu/Lark 为授权用户提供会话、卡片、图片和澄清工具。 |
| `--mode simulation` | 用 LIBERO 或 RoboTwin 2.0 这类仿真环境连接基础策略。 |
| `--mode robot` | 通过部署包接入真实机器人 SDK、策略、感知和评估器。 |
| Python library | 在自己的应用中直接组合 `Harness` 并调用 `run_stream()`。 |

## 安全边界

Thea 是编排层，不是经过认证的机器人安全系统。急停、关节/底盘限制、碰撞
避免和低层运动控制必须独立于模型运行。部署方还需要管理日志中的用户文本、
模型输出、工具参数和物理观测数据。

## 继续阅读

建议顺序：

1. [使用方式](usage.md)
2. [安装与配置](setup.md)
3. [Harness 核心机制](harness.md)
4. [接入真实机器人](port-to-your-robot.md)
5. [Scene Graph](scene-graph.md)
6. [Evaluation as Exit Codes](evaluation.md)
