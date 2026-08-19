# 格式审计智能体（Format Auditor）

## 简介

对论文的**版式与写作规范**进行审查：结合 **PyMuPDF** 等进行的版面/布局分析与基于 **DeepSeek** 的语义检查，覆盖标题层级、图表引用、参考文献版式、标点与术语等（具体以规则库为准）。

## 在本系统中的位置

- **上游**：`orchestrator` 在评阅任务中 `POST` 至本服务默认 `http://127.0.0.1:8007/audit`。
- **下游**：可选写库（任务/规则表）；读库加载 `agent_rules`（失败时可回退 `rules.yaml`）。
- **与全局关系**：与其它 Agent 共享同一 PostgreSQL 时，须保证 `metadata.paper_id` 与调度器入库的 UUID 一致，以便按需从库拉取 `payload.content` 缺失时的正文。

## 工作流程

1. 接收标准审计请求（`request_id`、`metadata`、`payload.content`、`config` 等）。
2. 若 `payload.content` 为空，尝试按 `paper_id` + `chunk_id` 从数据库拉取正文。
3. 执行布局分析、语义规则引擎及（按配置）LLM 辅助检查。
4. 返回符合协议的 JSON：`result` 中含 `score`、`audit_level`、`comment`、`suggestion`，以及可选的 `audit_results` 细项。

## 架构要点

- 规则优先从数据库热加载，YAML 为兜底。
- LLM 提供商在环境中配置为 DeepSeek 兼容接口。

## 启动与配置

`.env` 示例：

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com

DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=postgres
DB_USER=admin
DB_PASSWORD=你的密码
```

```bash
pip install -r requirements.txt
python main.py
```

默认监听 **8007**。更多设计说明见上级目录 `audit_format_docs/`。
