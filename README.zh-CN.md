<h1 align="center">Towards the Harness of Embodied Agents</h1>

<p align="center">
  <a href="https://eit-hai.github.io/thea/"><img src="docs/assets/button-project-page-globe.svg" alt="打开项目主页" height="30"></a>&ensp;
  <a href="https://arxiv.org/abs/2608.11246"><img src="docs/assets/button-paper.svg" alt="阅读论文" height="30"></a>&ensp;
  <a href="https://youtu.be/Sm9jFmfnOF0"><img src="docs/assets/button-youtube.svg" alt="在 YouTube 观看演示" height="30"></a><a href="https://www.bilibili.com/video/BV1q9uc6tEqJ/"><img src="docs/assets/button-bilibili.svg" alt="在 Bilibili 观看演示" height="30"></a>&ensp;
  <a href="https://eit-hai.github.io/thea/documentation/"><img src="docs/assets/button-docs.svg" alt="打开文档" height="30"></a>
  <sub><a href="https://eit-hai.github.io/thea/documentation/">English</a> · <a href="https://eit-hai.github.io/thea/documentation/zh-cn/index.html">中文</a></sub>&ensp;
  <a href="#引用"><img src="docs/assets/button-bibtex.svg" alt="跳转到 BibTeX" height="30"></a>
</p>

<p align="center">
  README:
  <a href="README.md">English</a>
  ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<h3 align="center">一句话：Thea 把代码智能体带到真实物理世界。</h3>

<br>

<p align="center">
  <img src="docs/assets/instruction.svg" alt="Instruction: &ldquo;Grab me a water.&rdquo;" width="760">
  <br>
  <a href="https://youtu.be/Sm9jFmfnOF0">
    <img src="https://github.com/EIT-HAI/Thea/releases/download/demo-v1/act1-preview.webp" alt="Thea demo: Grab me a water" width="760">
  </a>
</p>

## ⚡ 快速开始

需要 Python 3.10 或更新版本。

### 终端模式

克隆仓库，安装公开包，把所选模型的 API key 写入 `.env`，然后在本地终端
直接运行 Harness：

```bash
git clone https://github.com/EIT-HAI/Thea.git && cd Thea && ./install.sh
./run.sh --mode cli --check
./run.sh --mode cli
```

安装脚本会创建 `.venv`，安装 Harness、仿真接口、模型适配器和 Lark 通道，
并初始化配置文件。已有的 `.env` 和 `config.yaml` 不会被覆盖。终端模式会
使用 `config.yaml` 中选择的真实模型；它不会启动 Lark，也不要求仿真器或
真实机器人。若只想执行一个 Task，而不进入交互式提示符：

```bash
./run.sh --mode cli --instruction \
  "Notify the user that the Harness is ready, then explain what ran."
```

### 飞书/Lark

把模型 API key 和 Lark 应用凭证写入 `.env`，检查 `config.yaml`，然后验证
并启动通道：

```text
LARK_APP_ID=cli_...
LARK_APP_SECRET=...
LARK_ALLOWED_OPEN_IDS=ou_...
```

```bash
./run.sh --mode channel --check
./run.sh --mode channel
```

默认 WebSocket 传输不需要公网 callback URL。通道会加入按会话区分的
`query_user` 和 `notify_user` Tools。应用配置、webhook 传输、卡片、授权和
部署工厂函数见 [Lark guide](lark/README.md)。

### 仿真

安装 [LIBERO](https://lifelong-robot-learning.github.io/LIBERO/) 或
[RoboTwin 2.0](https://robotwin-platform.github.io/)，通过 Harness 工厂函数
连接一个 episode 和对应的基础策略，然后运行下面的命令。
`my_runtime.sim:create_harness` 是示例导入路径；运行前需要按照仿真指南
实现这个工厂函数。该模式会通过飞书/Lark 通道托管由仿真器支撑的 Harness，
因此也需要配置上一节中的 Lark 凭证：

```bash
./run.sh --mode simulation --check \
  --harness-factory my_runtime.sim:create_harness

./run.sh --mode simulation \
  --harness-factory my_runtime.sim:create_harness
```

episode、Observation、策略 Tool 和 evaluator 接口见
[simulation guide](simulation/README.md)。非 Lark 应用也可以在 Python 中
直接组合同一个 `Harness` 并调用 `run_stream()`。

### 真实机器人

机器人 SDK 和策略后端应放在部署包中。可以从
[`examples/port-template/`](examples/port-template/) 开始，然后验证并启动它
暴露的可导入工厂函数。`my_robot.factory:create_harness` 是示例导入路径，
必须指向部署包中的工厂函数。和仿真模式一样，机器人模式通过飞书/Lark
启动部署，因此也需要上面的通道凭证：

```bash
./run.sh --mode robot --check \
  --harness-factory my_robot.factory:create_harness

./run.sh --mode robot \
  --harness-factory my_robot.factory:create_harness
```

[Port to Your Robot](docs/harness/port-to-your-robot.md) 覆盖 Embodiment
Profile、Tools、Observation、Scene Graph、Evaluation 和资源归属。

### 只使用 Harness

如果应用只需要 Harness：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install './harness[anthropic]'
cp harness/config.example.yaml config.yaml
export ANTHROPIC_API_KEY=...
thea-cli --config config.yaml
```

## 🔧 使用 Harness 构建

Python 接口可以运行由指令驱动的 Tasks，并为能力扩展和任务流程提供清晰的
接入点。

### 运行一条指令

配置好 `Harness` 后，一条指令会启动一个 Task。流式事件会暴露模型决策、
Tool Calls、Tool Results、Evaluation 和最终结果：

```python
for event in harness.run_stream("Put bottle_1 on desk_1."):
    if event["type"] == "done":
        print(event["final_text"])
```

### 添加 Tool

可以把带类型注解的 Python 可调用对象注册成轻量级进程内能力：

```python
from harness import ToolRegistry


def register_robot_tools(registry: ToolRegistry, robot) -> None:
    @registry.tool(description="Return the robot's current battery percentage.")
    def get_battery() -> dict:
        return {"success": True, "observation": robot.battery_percent()}
```

Harness 会根据函数签名生成 Tool Definition。物理策略可以使用显式 schema、
后置条件，或由 MCP 支撑的 Tools；参见
[Models and Tools](harness/docs/model-and-tools.md)。

### 添加 Skill

Skill 将任务流程封装在 System Prompt 之外。默认只暴露目录中的名称和描述；
完整说明只会在相关 Task 中按需加载：

```text
skills/
└── tidy-workspace/
    └── SKILL.md
```

```markdown
---
name: tidy-workspace
description: Decide what to retain, remove, and report while tidying a desk.
---

## Procedure

Inspect the workspace before moving any object.
```

在 `config.yaml` 中把 `skills.dir` 指向 Skill 目录：

```yaml
skills:
  dir: ./skills
```

Harness 会把 name 和 description 保留在 Resident context 中，并暴露
`load_skill` 来加载仅当前 Task 有效的完整说明。资源和 Task
生命周期见 [Memory, Skills, and Embodiment](harness/docs/memory-and-skills.md)。

## 🧩 Harness 设计

公开模块沿用论文中的术语和边界：

| 论文组件 | 公开模块 | 作用 |
|---|---|---|
| Agentic Loop | `harness/runtime/` | 每轮最多执行一个由模型选择的 Tool Call，并从 Tool Result 继续。 |
| Context Engineering | `harness/context/` | 组装 Resident、Refreshed 和 Accumulated context，并压缩累计消息。 |
| Tool Protocol | `harness/tools/` | 发布 Tool Definitions、校验参数，并统一 Tool Results 的格式。 |
| Skills | `harness/skills/` | 让 Skill 的名称和描述常驻，并为当前 Task 加载被调用的 Skill 正文。 |
| Memory | `harness/memory/` | 按生命周期维护 Task Notes、durable Memory 和 Tool Experience。 |
| Safety | `harness/world/`, `harness/runtime/` | 刷新通行安全证据，并在模型之下执行确定性检查。 |
| User Interaction | `harness/terminal/`, `lark/` | 提供终端兜底能力，以及按会话区分的 `query_user` 和 `notify_user` 通道实现。 |
| Scene Graph as Context | `harness/world/` | 暴露持久、可按 ref 访问的世界状态、Scene Graph Brief 和按 ref 查询的接口。 |
| Evaluation as Exit Codes | `harness/evaluation/` | 在工具执行后根据物理后置条件进行评估。 |
| Embodiment Profile | `harness/world/` | 呈现稳定的机体能力、感知配置和相对于底座的位置。 |

[Harness 文档](docs/harness/index.md)解释这些组件及其公开集成边界。

## 📁 仓库结构

```text
.
├── harness/                 # 不绑定模型供应商的编排运行时
├── simulation/              # 可选的 LIBERO 与 RoboTwin 2.0 接口
├── lark/                    # 可选的飞书/Lark 通道
├── examples/                # 机器人接入模板
├── docs/                    # 文档站源码
├── install.sh               # 一键安装脚本
└── run.sh                   # 终端、通道、仿真和机器人入口
```

三个包可以独立安装：

- [`thea-harness`](harness/README.md) 包含核心运行时和公开部署边界。
- [`thea-simulation`](simulation/README.md) 包含由用户管理的仿真环境所需的
  Harness 侧适配接口。
- [`thea-lark`](lark/README.md) 将 Harness 会话连接到授权的飞书/Lark
  用户。

依赖方向保持向内：Lark 和 simulation 依赖 Harness，而 Harness 不导入任何
外部集成。

## 📚 文档

[documentation site](https://eit-hai.github.io/thea/documentation) 按集成任务
组织公开接口：

- [Usage](https://eit-hai.github.io/thea/documentation/usage/index.html)
- [Install & Setup](https://eit-hai.github.io/thea/documentation/setup/index.html)
- [Harness](https://eit-hai.github.io/thea/documentation/harness/index.html)
- [Scene Graph](https://eit-hai.github.io/thea/documentation/scene-graph/index.html)
- [Evaluation](https://eit-hai.github.io/thea/documentation/evaluation/index.html)
- [Reference](https://eit-hai.github.io/thea/documentation/reference/index.html)
- [中文文档](https://eit-hai.github.io/thea/documentation/zh-cn/index.html)

各包自己的指南包括 [Harness](harness/README.md)、
[仿真适配器](simulation/README.md) 和
[飞书/Lark 通道](lark/README.md)。

## 🛡️ 范围与安全说明

- 本仓库是 Thea Harness 的公开预览；它不绑定模型供应商，并提供可选的
  LIBERO/RoboTwin 2.0 接口和飞书/Lark 通道。
- 部署方需要提供自己的模型凭证、机器人 SDK 或仿真器、策略、感知后端、
  Scene Graph 后端和执行后证据。
- Harness 是编排层，不是经过认证的机器人安全系统。急停、执行器限制、碰撞
  避免和低层运动控制必须独立于模型。
- 日志可能包含用户文本、模型推理、工具参数和物理观测。部署方需要检查存储、
  访问和保留策略。

## 🤝 贡献

提交 issue 或 pull request 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
所有贡献必须遵守仓库的代码质量、测试和安全要求。

Thea 以 [Apache License 2.0](LICENSE) 发布。安全问题请按照
[SECURITY.md](SECURITY.md) 报告。

## 引用

如果本工作对你的研究有帮助，请引用：

```bibtex
@misc{thea2026,
  title  = {Towards the Harness of Embodied Agents},
  author = {Qi Wang and Tianyi Wang and Chengyang Li and Shikun Ban and Yurun Chen
            and Yizhong Ge and Jason Qin and Chengtai Li and Wentao Zhu},
  year   = {2026},
  note   = {Technical Report},
}
```
