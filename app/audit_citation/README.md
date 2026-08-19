# 文献审计智能体（Citation Auditor）

## 简介

审查论文中的**引用形态**与**参考文献**相关风险：如中英文混排、符号规范；在配置 **SerpAPI** 时可做外部检索辅助真实性判断；并可结合正文与参考文献节做编号一致性等启发式检查（实现以 `main.py` 为准）。

## 在本系统中的位置

- **上游**：调度器 `POST` 至默认 `http://127.0.0.1:8005/audit`，请求体含全文 `payload.content` 与 `paper_id`。
- **下游**：可选连接后端 `BACKEND_URL` 拉取结构化参考文献或全文（用于扩展检查）；结果可写入 `agent_audits`。
- **注意**：与调度器使用**同一数据库**时，`paper_id` 外键才能与 `papers` 表一致。

## 工作流程

1. 解析请求；若 `chunk_id` 为特殊值可走单条或全库参考文献分支（见路由逻辑）。
2. 常规路径下对 `content` 做规则扫描（混排、`【】`、参考文献节检测等）。
3. 若开启细则且配置了 SerpAPI，可对参考文献条目做检索节奏流控。
4. 返回统一 JSON 协议响应，供调度器聚合。

## 架构要点

- 大模型调用走 DeepSeek 兼容环境变量（如 `DEEPSEEK_API_KEY` / `LLM_API_KEY`）。
- 环境变量 `CITATION_MAX_REFERENCES` 等用于限制外检条数，避免超时。

## 启动与配置

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_API_KEY=your_api_key_here
SERPAPI_KEY=
```

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8005
```
