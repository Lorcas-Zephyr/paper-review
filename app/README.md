# 学术论文多智能体自动评阅系统

## 项目简介

本项目基于大语言模型（LLM）与多智能体（Multi-Agent）架构，对学术论文进行自动化评审。典型路径为：将 PDF 转为 Markdown（或直接提交 Markdown），由**调度中心**完成入库与向量化，**四个专业审计 Agent** 并行审查，再由**反思评估服务**做冲突处理、证据校验、综合打分与报告生成。底层模型统一采用 **DeepSeek** 兼容接口；论文切片与向量使用 **PostgreSQL（pgvector）** 持久化。

更细的密钥说明见根目录 `ENV_API_KEYS.md`。

## 系统架构

各微服务默认端口如下（可在各子项目配置中修改，需与调度器、前端代理一致）。

| 服务 | 目录 | 作用 | 默认端口 |
|------|------|------|----------|
| 前端 | `website` | 上传、展示评阅结果与配置项 | 3000 |
| 调度中心 | `orchestrator` | 入库、并发调 Agent、聚合、调反思 API | 7860 |
| 逻辑审计 | `audit_logic` | 语义/规则一体化逻辑审计 | 8008 |
| 实验审计 | `audit_experiment` | 实验与统计表述审查 | 8006 |
| 格式审计 | `audit_format` | 版式与语义格式审查 | 8007 |
| 文献审计 | `audit_citation` | 引用与参考文献相关审查 | 8005 |
| 反思评估 | `audit_reflection` | 冲突裁决、综合分、Markdown 报告 | 8009 |
| PDF 上传 | `pdf_api` | 接收并保存上传文件 | 5000 |
| PDF→MD | `pdf_to_md` | 将 PDF 转为 Markdown | 8002 |

可选组件：`paper_review_api` 提供本地 Mock，便于无真 Agent 时联调；`audit_code` 为代码片段审计实验模块，默认不接入主线四 Agent。

## 端到端工作流程

1. **文稿进入系统**
   用户在前端上传 PDF：经 `pdf_api` 落盘，再经 `pdf_to_md` 得到 Markdown；也可使用前端「加载测试 Markdown」跳过 PDF 环节。

2. **提交评阅任务**
   前端向调度器 `POST /api/v1/audit` 提交标题与全文 `content`（及可选 `config`：如是否开启审计细则、导师对话等）。

3. **调度器处理**
   - 调用 `PaperProcessor`：清洗文本、按标题分节写入 `papers` / `paper_sections`，并生成章节向量（BERT）。
   - 并发 HTTP 调用四 Agent，请求体携带同一 `paper_id` 与全文 `content`（逻辑组另走一体化接口时可附带 `payload.content`）。
   - 收集各组 JSON 结果，做加权聚合后调用反思服务 `REFLECTION_API_URL`（默认 `http://127.0.0.1:8009`）的 `/api/evaluate/inline`。
   - 将反思返回的综合分、结论、问题列表等合并进任务结果，供前端轮询 `GET /api/v1/task/{request_id}` 拉取。

4. **反思评估（概要）**
   读取各组结果与（可选）数据库规则，进行优先级排序、冲突裁决、证据与幻觉过滤、严重度校准，按百分制四档生成结论文本，并可生成 `audit_reflection/reports` 下的 Markdown 报告。


## 部署与运行

### 环境准备

- Python 3.10+、Node.js（LTS）
- PostgreSQL，且安装 **pgvector**
- 连接信息以各服务 `.env` 为准；仓库示例中曾使用内网库 `127.0.0.1:5432`（以你方实际环境替换）

### 配置密钥

复制 `.env.example` 为 `app/.env`，在这一处填写 `DEEPSEEK_API_KEY`；模型、Base URL、超时和 JSON 模式也统一从该文件读取。不要把真实密钥提交到仓库。

### 一键启动与停止

在仓库根目录（Windows 可用 Git Bash）：

```bash
./start.sh
```

浏览器访问 `http://localhost:3000`。停止：

```bash
./stop.sh
```

## 子项目文档

各目录下的 `README.md` 说明该模块的**职责、在本流程中的位置、对外接口与启动方式**。从调度器接入顺序阅读时建议：`orchestrator` → 四 Agent → `audit_reflection` → `website` → PDF 链路。

LLM 提示词归档（只读副本，便于写文档）：见 **`prompt/`** 目录及其中 `README.md`；**运行时不自动加载该目录**，以各源码中的定义为准。
