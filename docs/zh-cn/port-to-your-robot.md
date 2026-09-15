# 接入真实机器人

Thea 的公开仓库不包含某个具体机器人的 SDK、策略或私有部署代码。真实机器人
接入应放在单独的部署包中，并通过工厂函数返回一个配置好的 `Harness`。

## 部署包负责什么

部署包通常需要提供：

- 机器人 SDK 连接和资源生命周期管理。
- 当前观测：图像、深度、时间戳、来源信息和必要的文本摘要。
- 可执行策略：如 navigation、pick、place、open、close 等基础 Tools。
- 安全边界：急停、速度/力矩限制、碰撞避免和 base clearance checks。
- Scene Graph 后端：对象 refs、位置、关系、存储图片和新鲜度。
- Evaluation 证据：每次物理 Tool 运行后的客观证据，以及评估器。
- 一个可导入的工厂函数，例如 `my_robot.factory:create_harness`。

Harness 只负责把这些能力放进统一循环里运行。

## 工厂函数形态

工厂函数应创建或接收部署资源，注册 Tools，并返回 `Harness`：

```python
from harness import Harness, ToolRegistry


def create_harness():
    robot = connect_robot()
    registry = ToolRegistry()

    register_robot_tools(registry, robot)

    return Harness(
        load_config(),
        model=build_model(),
        registry=registry,
        observation_provider=RobotObservationProvider(robot),
        base_clearance_provider=RobotClearanceProvider(robot),
        scene_graph=RobotSceneGraph(robot),
        evaluator=build_evaluator(),
        post_execution_observation_provider=RobotRunObserver(robot),
        owned_resources=(robot,),
    )
```

启动前先运行启动检查：

```bash
./run.sh --mode robot --check \
  --harness-factory my_robot.factory:create_harness
```

然后启动通道：

```bash
./run.sh --mode robot \
  --harness-factory my_robot.factory:create_harness
```

## Tool 设计

物理 Tool 应满足三个原则：

- 输入使用稳定 refs 或明确参数，不让模型传入部署本地路径或不受控命令。
- 返回结构化 Tool Result，并为需要 Evaluation 的 Tool 返回非空 `run_id`。
- Tool 自身只报告后端执行状态，不把它当作物理成功判定。

例如 `pick_object(target)` 可以要求 `target` 是最新 Scene Graph Brief 中已有的
ref。执行后返回 `run_id`，后续由 Evaluation 根据执行后 Observation
判断是否真的拿起目标。

## Observation 与 Safety

`ObservationProvider` 提供当前证据。它应返回与具体后端解耦的内容，而不是
泄露部署本地路径。图像可以带 name、media type、新鲜度、timestamp 和来源信息。

如果机器人支持 base motion，建议接入 `BaseClearanceProvider`，为每轮决策提供
四方向 clearance。模型只能看到证据；真正的急停、速度限制和碰撞检查必须
在模型之外独立执行。

## Scene Graph 与 Evaluation

真实机器人部署建议同时接入：

- [Scene Graph](scene-graph.md)：为对象、容器、机器人 pose 和关系提供持久 refs。
- [Evaluation as Exit Codes](evaluation.md)：在物理 Tool 后用客观证据判断
  `process`、`success` 或 `failure`。

执行后对 Scene Graph 的更新应由评估器确认的 success 触发。失败判定不应
写入“已经拿起/已经放入”等由执行结果推导出的状态。

## 验证顺序

建议按这个顺序验证：

1. `./run.sh --mode robot --check` 能导入工厂函数。
2. Observation 能返回当前图像和摘要，且不包含本地私有路径。
3. Scene Graph Brief 中 refs 唯一、位置和新鲜度有效。
4. Tool 对未知 ref 或非法参数会拒绝。
5. 每个 evaluated Tool 都返回稳定 `run_id`。
6. Post-execution Observation 能根据 `run_id` 找到客观证据。
7. Evaluator 的 `success` 才会触发由执行结果推导出的 Scene Graph 更新。
8. 急停、限速和碰撞避免不依赖模型输出。
