# Evaluation as Exit Codes

Evaluation as Exit Codes 用来补上物理执行不会自动给出的结果信号。配置过的
物理 Tool 完成后，Harness 会通过隐藏的执行后 hook 调用独立评估器。

评估器只接收：

1. 被选中 Tool 的后置条件；
2. 与该 Tool 的 `run_id` 关联的执行后 `Observation`。

它不会接收执行模型的 reasoning、自我报告、当前用户指令或后端结果字段。

## 公开版本提供什么

| Thea 提供 | 部署方提供 |
|---|---|
| 隐藏 `evaluate_run` Tool 和结构化执行后触发逻辑 | 每个被评估物理 Tool 返回非空 `run_id` |
| `EvaluatorProtocol` 和类型化三态判定 | 自定义评估器或配置给 `ModelEvaluator` 的模型 |
| `PostExecutionObservationProvider` | 从 `run_id` 解析客观执行后证据 |
| 每个 Tool 的后置条件边界 | 每个物理 Tool 的真实成功标准 |
| 对 `process`、`success`、`failure` 的校验 | 分段执行策略的语义 |
| success-gated Scene Graph update | 部署侧由执行结果推导出的图状态变化 |

公开包不包含机器人产物目录、策略日志格式、相机同步逻辑或通用
后置条件。

## 判定结果协议

```python
from harness import EvaluatorVerdict

verdict = EvaluatorVerdict(
    status="failure",
    evidence=(
        "the bottle remains on the table",
        "the gripper closed without lifting it",
    ),
    failure_reason="the robot stopped too far from the bottle to grasp it",
)
```

`status` 只能是：

- `process`：执行应继续进入另一个动作片段；
- `success`：后置条件已满足；
- `failure`：执行应停止，失败原因返回 Agentic Loop。

只有列在 `evaluation.segment_tools` 中的 Tool 才能返回 `process`。

## 绑定后置条件

进程内 Tool 可以直接携带判定标准：

```python
from harness import BuiltinTool

registry.register(
    BuiltinTool(
        name="pick_object",
        description="Pick one confirmed Scene Graph ref.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        fn=pick_object,
        post_condition=(
            "The requested object is visibly lifted and held by the gripper."
        ),
    )
)
```

MCP Tool 可以在配置中按名称映射同样的协议：

```yaml
evaluation:
  required_tools: [pick_object]
  segment_tools: []
  max_segments: 8
  post_conditions:
    pick_object: >-
      The requested object is visibly lifted and held by the gripper.
```

每个被评估 Tool 都必须返回非空 `run_id`，即使后端自己报告失败。后端状态不是
物理判定结果。

## 连接执行后证据

部署方决定 `run_id` 如何解析为证据：

```python
from harness import Observation


class MyPostExecutionObservationProvider:
    def capture(self, run_id: str) -> Observation:
        return capture_observation_for_run(run_id)
```

返回的 `Observation` 不应包含本地路径，也不应绑定某个后端实现。它可以携带命名
相机视图、base clearance、timestamp、来源信息和紧凑摘要。`ModelEvaluator`
默认最多转发三个命名视觉视图。

## 选择评估器

可以使用内置的模型评估器：

```python
from harness import ModelEvaluator
from harness.models import build_model

evaluator = ModelEvaluator(
    build_model(
        "openai",
        model="gpt-4o-mini",
    )
)
```

也可以实现确定性、学习型或服务后端支撑的评估器：

```python
from harness import EvaluationRequest, EvaluatorVerdict


class MyEvaluator:
    def evaluate(self, request: EvaluationRequest) -> EvaluatorVerdict:
        return judge_post_condition(
            request.post_condition,
            request.post_execution_observation,
        )
```

实现必须返回 `EvaluatorVerdict`。

## 组合 Harness

```python
harness = Harness(
    config,
    registry=registry,
    evaluator=evaluator,
    post_execution_observation_provider=(MyPostExecutionObservationProvider()),
    scene_graph=scene_graph,
)
```

调用链如下：

```text
evaluated physical Tool
  -> automatic post-execution hook
  -> hidden evaluate_run(run_id, target_tool_name)
  -> PostExecutionObservationProvider.capture(run_id)
  -> EvaluatorProtocol.evaluate(post-condition, Observation)
  -> process | success | failure
```

模型不能选择 `evaluate_run`。Harness 从完成的物理 Tool 派生该调用，并且不会把
它的 Tool Definition 暴露给模型。

## 复现完整行为

在另一台机器人或策略上复现时：

1. 为每个被评估 Tool 定义可见、可测试的后置条件。
2. 让 Tool 在物理执行开始后返回稳定 `run_id`。
3. 保留判断该 run 所需的证据。
4. 实现 `capture(run_id)`，返回类型化的执行后 `Observation`。
5. 配置 `ModelEvaluator` 或实现 `EvaluatorProtocol`。
6. 将 Tool 加入 `evaluation.required_tools`。
7. 如果 Tool 分段执行，将其加入 `evaluation.segment_tools` 并设置 `max_segments`。
8. 验证只有 success 才能触发 Scene Graph 执行更新。
