# 实验数据审计智能体（Experiment Agent）

## 简介

针对论文中**实验设计、结果报告与统计表述**进行审查：如显著性（P 值）、指标完整性、图表与正文一致性等，结合 **DeepSeek** 与（可选）数据库中的专家规则或 Mock 降级数据。

## 在本系统中的位置

- **上游**：调度器 `POST` 至默认 `http://127.0.0.1:8006/audit`，请求体含 `paper_id` 与全文 `content`（无正文时可从专家库拉取，见服务端逻辑）。
- **下游**：可将结果写入 PostgreSQL `agent_audits`；依赖 `EXPERT_DB_*` 或等价变量访问规则/内容库。

## 工作流程

1. 接收审计请求，确定用于推理的正文来源（请求体优先）。
2. 调用 LLM 与/或规则对实验章节做结构化检查，输出分项 `audit_results` 与综合 `score`、`audit_level`。
3. 异步或通过回调将结果持久化（若配置开启）。
4. 返回 JSON，由调度器纳入聚合与反思输入。

## 架构要点

- 与项目统一使用 DeepSeek 兼容 API。
- 数据库不可用时部分路径有演示用 Mock，**生产环境应保证库与规则可用**。

## 启动与配置

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com

EXPERT_DB_HOST=127.0.0.1
EXPERT_DB_PORT=5432
EXPERT_DB_NAME=postgres
EXPERT_DB_USER=admin
EXPERT_DB_PASSWORD=你的密码
```

```bash
pip install -r requirements.txt
python group6_api_server.py
```

默认端口 **8006**。API 细节可参考同目录 `api_documentation.md`。
