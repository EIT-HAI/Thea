# Scene Graph

Scene Graph 让物理环境在多个 Turns 之间保持可读、可引用。当前图像和深度只
描述某一时刻的某个视角；working graph 会把这些证据组织成持久对象 refs、
粗粒度空间状态、机器人状态、类型化关系和新鲜度信息。

Harness 不规定检测器、建图模块、数据库或机器人 SDK。这些系统都位于
`SceneGraphProtocol` 后面。

## 公开版本提供什么

| Thea 提供 | 部署方提供 |
|---|---|
| `SceneGraphProtocol`：刷新、生成 Brief、应用已确认的执行更新 | 感知、定位、跟踪、关联和图存储 |
| `SceneGraphBrief` 和面向决策的类型化记录 | 从 working graph 到这些 records 的转换 |
| Brief 到 Refreshed context 的标准渲染 | Brief 使用的坐标系和来源信息 |
| `SceneGraphQueryProtocol` | 根据已有 ref 查询关系和存储图像 |
| `get_object_relations` 和 `get_image` 注册 | 底层 relation edges 和视觉证据 |
| 由评估器判定后才触发的执行更新调用 | 成功 Tool 导致的部署侧状态变化 |

公开包复现的是 Harness 侧协议，不是一套完整的场景理解系统。

## Working Graph 与 Scene Graph Brief

部署侧 working graph 可以包含细粒度几何、所有 relation edges、存储视觉证据和
后端内部状态。每轮都把完整 working graph 给模型会浪费大量上下文。

`SceneGraphBrief` 是放入 Refreshed context 的字段选择投影。它保留每个对象
ref，但只选择决策常用字段：

```python
from harness import (
    SceneGraphBrief,
    SceneGraphObjectBrief,
    SceneGraphRobotBrief,
)

brief = SceneGraphBrief(
    objects=(
        SceneGraphObjectBrief(
            ref="cup_2",
            coarse_position=(3.91, -0.18, 1.08),
            confidence=1.0,
            freshness="current",
        ),
    ),
    robot=SceneGraphRobotBrief(
        pose=(0.0, 0.0, 0.0),
        holding=(),
    ),
    freshness="current",
    provenance="rgbd_scene_graph",
    coordinate_frame="map",
    updated_at="2026-07-27T12:00:00Z",
)
```

对象记录还可以携带 `container_state` 和被跟踪的 `contents`。working graph 可以
编码 `on`、`inside`、`holding`、`near` 等关系。默认情况下，relations 和
stored images 不进入 Brief，而是通过按 ref 查询的 Tools 暴露。

紧凑性来自字段选择，而不是剪掉节点。

## 接入现有 Scene Graph

实现持久世界状态边界：

```python
from harness import SceneGraphBrief


class MySceneGraph:
    def refresh_from_perception(self) -> None:
        merge_latest_perception_into_working_graph()

    def brief(self) -> SceneGraphBrief:
        return project_working_graph_to_brief()

    def apply_confirmed_execution(
        self,
        *,
        tool_name,
        arguments,
        result,
        evaluator_verdict,
    ):
        return apply_execution_update(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            verdict=evaluator_verdict,
        )
```

传给 `Harness`：

```python
harness = Harness(
    config,
    scene_graph=scene_graph,
    observation_provider=observation_provider,
    registry=registry,
)
```

每次模型调用前，Harness 会调用 `refresh_from_perception()`，读取 `brief()`，并
重新生成放入 Refreshed context 的文本。working graph 本身会跨调用保留。

## 添加按 ref 查询的 Tools

同一个 provider 可以暴露被 Brief 省略的细节：

```python
from harness import (
    SceneGraphRelation,
    VisualEvidence,
    register_scene_graph_query_tools,
)


class MySceneGraph:
    def has_ref(self, ref):
        return ref in self.objects

    def get_object_relations(self, ref, relation=None):
        return (
            SceneGraphRelation(
                relation="near",
                other_ref="desk_12",
                distance_m=0.4,
            ),
        )

    def get_image(self, ref):
        return (
            VisualEvidence(
                name="stored-front",
                data=read_stored_image(ref),
                media_type="image/jpeg",
                freshness="last_seen",
            ),
        )


register_scene_graph_query_tools(registry, scene_graph)
```

注册出的 Tools 使用 `obj` 作为模型可见参数，其值必须是已有 Scene Graph ref：

- `get_object_relations(obj, relation=None)`
- `get_image(obj)`

两者都要求 ref 已经出现在最新 Brief 中。存储视觉证据可能是历史证据；当前操作
证据应来自最新 Observation。

## 兼容后端需要什么

兼容后端至少需要以下 pipeline：

1. 获取 RGB、depth、pose 和其他定位输入。
2. 检测或分割实体，并在声明坐标系中估计粗粒度几何。
3. 跨帧关联观测，让同一实体尽可能保持同一个 ref。
4. 维护对象、容器、机器人 pose、holding state、新鲜度、来源信息、时间戳和类型化关系。
5. 保留 ref-to-image 索引，返回不暴露本地路径的 `VisualEvidence`。
6. 通过 `SceneGraphBrief` 返回所有 refs 和选中的粗粒度字段。
7. 只有当 Harness 传入评估器确认的 success 时，才应用由执行结果推导出的更新。

[SysNav](https://arxiv.org/abs/2603.06914) 是构建真实世界 structured scene
representation 的有用系统参考，但不是 Thea 的依赖。任意后端只要满足公开
protocols，都可以接入。

## 验证集成

按以下顺序检查：

1. `brief()` 返回唯一 refs 和合法 confidence。
2. `render_scene_graph_brief(brief)` 列出模型应看到的全部 refs。
3. `get_object_relations` 和 `get_image` 拒绝未知 refs。
4. 感知 refresh 后 Brief 会重新生成。
5. 评估器给出失败判定时，不改变由执行结果推导出的图状态。
6. 成功判定只应用一次部署定义的更新。

Scene Graph 识别实体和粗略位置；它不判断当前 pose 是否适合操作，也不判断动作
是否成功。
