# 调度中心（Orchestrator）

## 简介

调度中心是论文自动评阅链路的**控制面**：接收前端提交的标题与 Markdown 全文，完成**入库与向量化**，**并发调用**四个审计 Agent，将原始返回与元数据聚合后请求**反思评估服务**，最后把综合结果写入任务状态供前端轮询。

## 在本系统中的位置

- **上游**：`website`（`POST /api/v1/audit`）、可选经 `pdf_api` / `pdf_to_md` 得到的正文。
- **下游**：PostgreSQL；文献/实验/格式/文献四服务（默认 8005–8008）；`audit_reflection`（默认 8009，`reflection_bridge.py` 中 `REFLECTION_API_URL` 可配置）。
- **本进程默认端口**：7860。

## 工作流程（调度器视角）

1. 解析请求体 `PaperSubmission`（`title`、`content`、`config`）。
2. 在线程池中执行 `PaperProcessor.process_paper_from_content`：去 NUL、清洗、分节写入 `paper_sections`，生成 `paper_id`（UUID）。
3. 使用 `httpx.AsyncClient(trust_env=False)` 并发调用各 `agent_endpoints`：为每组构造符合协议的 JSON（含 `paper_id`、全文 `content` 或逻辑组一体化 body）。
4. 将成功返回整理为 `aggregate_results` 中的分组结构；再调用 `run_reflection_evaluation` 将 `audit_groups` 与 `paper_content` POST 至反思服务。
5. 用 `merge_aggregate_and_reflection` 将反思结果合并进对外报告；更新任务 `overall_status` 与 `aggregated_report`。

## 架构要点

- **逻辑组**：配置中既有 `/audit/paper`，一体化路径为 `/audit/integrated?paper_id=...`，请求体中带 `payload.content` 与库内全文对齐。
- **分数与等级**：组级展示可用 `audit_level_from_score` 与分数映射；部分 Agent 分数嵌套在 `audit_results` 内，调度器会尝试抽取。
- **超时**：单 Agent 请求超时较长（如 600s），适配长文与模型推理。

## 启动与配置

中枢依赖数据库（与 `paper_processor` 一致），`.env` 示例：

```env
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=admin
POSTGRES_PASSWORD=你的密码
```

启动（在 `orchestrator` 目录）：

```bash
python orchestrator.py
```

主要 HTTP 接口：

- `GET /health`
- `POST /api/v1/audit`：提交任务，返回 `request_id`、`paper_id`
- `GET /api/v1/task/{request_id}`：查询进度与聚合结果

详见代码内 `Orchestrator` 与 FastAPI 路由定义。
