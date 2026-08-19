# 反思组 Member A（数据访问）

## 角色

为反思评估链路提供 **PostgreSQL 访问封装**：读取审计历史、规则关联数据及论文片段等，供其它模块构造冲突裁决与报告输入。不单独暴露 HTTP 端口。

## 在架构中的位置

位于 **audit_reflection** 内部依赖层：由 `src/db/database.py`、`ReflectionOrchestrator` 等统一调用；与调度器写入的 `papers` / `paper_sections` / `agent_audits` 等表在同一数据库实例上协作。

## 工作流中的环节

在 `process_paper` 或 HTTP `/api/evaluate/inline` 处理过程中，当需要从库拉取规则、论文全文或持久化反思结果时，经本层封装访问数据库。

## 使用说明

无需在本目录单独启动服务。修改连接串时与项目根及其它服务的 `.env` / 配置保持一致。单测连通性可参考 `src/db` 相关代码与 `docs/快速启动指南.md`。
