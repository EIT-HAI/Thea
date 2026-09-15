# 参考资料

本页提供中文索引。具体 API、配置字段和维护细节仍以英文 reference 页面为准。

## 配置

[Configuration](../reference/configuration.md) 记录可信 YAML 配置面，包括：

- `llm` provider、model、API key 环境变量和 token 限制；
- MCP servers；
- context compaction；
- Skills 目录；
- Embodiment Profile 文件；
- safety 和 base clearance；
- Evaluation 的 `required_tools`、`segment_tools`、`post_conditions`。

## API

[API](../reference/api.md) 按责任划分公开 Python 接口，包括：

- `Harness` composition root；
- model protocols 和 provider adapters；
- Tool registry、schemas、Tool Result；
- Observation、Scene Graph、Evaluation；
- Memory、Skills、terminal runtime。

部署侧应依赖这些公开接口，而不是导入私有实现细节。

## 仿真

[Simulation](../reference/simulation.md) 描述可选 LIBERO 和 RoboTwin 2.0 适配器。
Thea 侧提供 Harness 侧适配接口；benchmark 环境、episode 和策略仍由部署侧
安装和管理。

## 飞书/Lark

[Feishu/Lark](../reference/lark.md) 描述用户通道、授权、消息路由和交互 Tools。
默认 WebSocket 传输不需要公网 callback URL；Webhook 部署需要额外配置
verification token 或 encrypt key。

## 故障排查

[Troubleshooting](../reference/troubleshooting.md) 覆盖常见启动和运行失败，例如：

- 配置文件缺失或 YAML 无效；
- 模型凭证未设置；
- Lark app credentials 不完整；
- 工厂函数导入路径无法解析；
- evaluated Tool 没有返回 `run_id`；
- 评估器或执行后 observation provider 未配置。

## 文档维护

[Maintain the documentation](../maintain.md) 说明如何本地构建和发布文档站：

```bash
python -m pip install -r docs/requirements.txt
make -C docs html
```

文档构建将 Sphinx warnings 视为 errors，因此导航、include、链接和 theme 配置
问题会在发布前失败。
