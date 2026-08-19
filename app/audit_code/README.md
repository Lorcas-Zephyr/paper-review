# 代码审计智能体（Code Review Agent）

## 简介

面向论文中出现的**代码片段、算法描述与实现细节**，通过 **CrewAI** 等工作流组织多步审查（规范、复杂度、张量形状等），底层调用 **DeepSeek** 兼容接口。本模块在主线「四 Agent 评阅」中**默认未接入调度器**，用于扩展或单独演示。

## 在本系统中的位置

- **定位**：可选微服务；若接入总线，需在 `orchestrator` 中增加 `agent_endpoints` 并与前端矩阵约定组名。
- **数据**：可通过 `db.py` 等从 PostgreSQL 读取论文片段，配置须与项目其它服务一致。

## 工作流程（概念）

1. 获取待审代码上下文（文件或数据库片段）。
2. CrewAI Agent 链依次执行静态与语义类检查。
3. 汇总为结构化结论与建议，返回 JSON 或写入审计表（视部署而定）。

## 启动与配置

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat
```

安装依赖后按本目录入口脚本启动；具体命令以仓库内 `main` 或启动说明为准。
