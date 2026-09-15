# Harness 核心机制

Harness 是模型和当前 embodiment 之间的运行时层，不绑定某个模型供应商或机器人
平台。它负责调度和上下文组织；部署方负责机器人或仿真器连接、感知后端、策略和
硬件安全。

## 主要组件

| 组件 | 责任 |
|---|---|
| Agentic Loop | 刷新证据，请求一次模型决策，最多执行一个被选中的 Tool，并从结果继续。 |
| Context | 把 Resident、Refreshed 和 Accumulated 信息放在不同生命周期中维护。 |
| Tool Protocol | 发布 Tool Definitions、校验参数、统一 Tool Results 格式，并执行 hooks。 |
| Memory | 维护 Task Notes、durable Memory 和 Tool Experience。 |
| Skills | 让 Skill 的名称和描述常驻，只在需要时加载完整任务指令。 |
| Observation | 为当前决策提供视觉证据；配置 base motion 时提供四方向 clearance。 |
| Safety | 在模型之下执行确定性的执行前检查。 |
| User Interaction | 通过终端或飞书/Lark 提供 `query_user` 和 `notify_user`。 |
| Embodiment Profile | 呈现稳定能力、感知配置和 base-relative positions。 |

Scene Graph 和 Evaluation 是两个一等物理世界组件：

- [Scene Graph](scene-graph.md) 让世界状态可读、可引用。
- [Evaluation as Exit Codes](evaluation.md) 在 Tool 执行后判断物理结果。

## 一轮决策边界

Harness 每轮只把当前需要的内容放进模型上下文：

- Resident context：长期稳定内容，如系统边界、Skill 目录、Embodiment
  Profile 和 durable Memory。
- Refreshed context：每轮刷新内容，如当前 Observation、clearance、Scene
  Graph Brief。
- Accumulated context：当前 Task 的消息、Tool Calls、Tool Results 和压缩
  后的历史。

每个 Turn 最多执行一个被模型选中的 Tool Call。Tool Result 回到 Harness 后，
下一轮会重新刷新证据，而不是让模型继续依赖过期观察。

## Tool 与执行边界

Tool 是模型可以选择的动作或查询接口。Harness 负责：

- 将 Python 可调用对象、`BuiltinTool` 或 MCP 能力暴露成 Tool Definition。
- 校验模型传入的参数。
- 标准化 Tool Result。
- 在物理 Tool 后触发 hooks，例如 Evaluation。
- 将 Tool Result 记录进 Task context。

物理策略、机器人 SDK、MCP 服务和授权逻辑仍属于部署侧。Harness 不应该绕过
这些边界直接操作硬件。

## Skill、Memory 与 Embodiment

Skill 用于存放任务流程，不是执行权限边界。默认只有 name 和
description 进入 Resident context；模型需要时通过 `load_skill` 加载完整
`SKILL.md`。

Memory 分为三类：

- `TASK_NOTES.md`：当前 Task 中的短期笔记，Task 结束后过期。
- `MEMORY.md`：跨 Task 保留的 preferences、conventions 和 lessons。
- `tool_experience/`：按 Tool 组织的成功/失败经验。

Embodiment Profile 是部署侧提供的 Markdown 文档，描述 base footprint、
mobility、reachable workspace、sensor modalities、模型可见视角、camera
positions 和 initial gripper positions。Harness 在启动时验证结构。

## 组合边界

主要组合入口是 `Harness`。部署侧通过公开 protocols 注入能力，而不是让
Harness 导入机器人专用运行时：

```python
from harness import Harness

harness = Harness(
    config,
    model=model,
    registry=registry,
    observation_provider=observation_provider,
    base_clearance_provider=base_clearance_provider,
    scene_graph=scene_graph,
    evaluator=evaluator,
    post_execution_observation_provider=post_execution_observer,
    owned_resources=(robot,),
)
```

新的机器人或仿真环境应优先从 [接入真实机器人](port-to-your-robot.md) 中可直接
复用的集成路径开始。
