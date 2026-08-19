# 反思评估组 — 扩展说明与文档索引

## 与主 README 的关系

- **日常接入**：以仓库内 **`audit_reflection/README.md`** 为准（HTTP 服务、端口、与调度器的数据流）。
- **本文档**：补充**模块清单**与**文档导航**，便于在仓库内快速定位设计细节与验证步骤。

## 架构与数据流（摘要）

调度器将 `audit_groups`（含四组 Agent 的 JSON）与 `paper_content` POST 至 **`/api/evaluate/inline`**。反思服务读取数据库规则（`main_rules` / `rule_judge`），执行优先级排序、冲突裁决（含 LLM）、证据校验与严重度校准，输出 `final_score`、`verdict`、分级问题列表，并可写入 **`reflect_agent_verdict`**、生成 **`reports/*.md`**。CLI 模式 **`python run.py`** 支持从文件或数据库离线跑通同一套逻辑。

## 核心模块（代码位置）

| 能力 | 主要路径 |
|------|----------|
| HTTP 入口 | `main.py` |
| 编排与打分 | `run.py` → `ReflectionOrchestrator` |
| 冲突裁决 | `src/conflict_resolution/conflict_resolver.py` |
| 优先级排序 | `src/priority_sorting/review_engine.py` |
| 规则读取 | `src/db/database.py`（`fetch_rules` 等） |
| 四档结论 | `src/common/thesis_grade_verdict.py` |
| Markdown 报告 | `src/common/report_generator.py` |
| 严重度校准 | `src/common/severity_calibration.py` |

## 进一步阅读（docs/）

- `docs/快速启动指南.md`
- `docs/功能验证清单.md`
- `docs/最终使用说明.md`
- `docs/项目结构说明.md`

## 历史说明

本文件曾包含超长功能列表；现已**收敛为索引**，避免与代码漂移重复。若需组内周计划、接口需求等背景材料，仍可在 `docs/` 与根目录其它 `.md` 中查找。
