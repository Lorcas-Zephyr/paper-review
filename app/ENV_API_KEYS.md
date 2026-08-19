# 密钥与环境变量

当前仓库内 **LLM/API** 与 **PostgreSQL** 的密钥已统一到同一取值时，各服务通过 **`.env`** 读取（见 `orchestrator/.env`、`audit_format/.env`、`audit_experiment/.env` 等）。

说明：

- **`sk-...` 形态**：通常用于 DeepSeek / OpenAI 兼容接口；**SerpAPI**、**Google** 等第三方仍使用其官网申请的专用 Key，不能随意用 `sk-` 字符串代替。
- **数据库 `DB_PASSWORD` / `POSTGRES_PASSWORD` / `EXPERT_DB_PASSWORD`**：必须与 **PostgreSQL 上对应用户真实密码** 一致；若与服务器不一致会出现 `InvalidPasswordError`。格式审计在 DB 写入失败时仍会返回审计结果（见 `audit_format/main.py` 中 `save_result_to_db`），但落库会失败。
- **勿将** `.env` **提交到 Git**（已加入 `.gitignore`）。

| 目录 | 主要变量 |
|------|----------|
| `orchestrator/` | `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| `audit_format/` | `DEEPSEEK_API_KEY`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, … |
| `audit_experiment/` | `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `EXPERT_DB_*` |
| `audit_citation/` | `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `LLM_API_KEY`, `SERPAPI_KEY`（可选） |
| `audit_reflection/` | `DEEPSEEK_API_KEY`, `LITELLM_API_KEY` |
| `audit_code/` | `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` |
