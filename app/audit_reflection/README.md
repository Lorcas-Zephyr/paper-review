# 反思评估服务（Reflection）

## 简介

在多智能体分项审计完成后，本服务对**各组 JSON 结果**进行优先级排序、**冲突检测与裁决**（含 LLM 辅助）、**证据与幻觉过滤**、严重度校准，计算**综合分**，并按**百分制四档**（优秀≥90、良好80–89、一般70–79、较差<70）生成结论文本；可选生成导师对话与 **Markdown 评审报告**（`reports/`）。

## 在本系统中的位置

- **上游**：`orchestrator` 通过 HTTP `POST /api/evaluate/inline` 传入 `paper_id`、`paper_title`、`paper_content`、`audit_groups` 等（见 `reflection_bridge.py`）。
- **下游**：PostgreSQL（规则表 `main_rules` / `rule_judge`，结果表 `reflect_agent_verdict` 等，以实际迁移为准）。
- **默认端口**：8009（`main.py` 中可通过环境变量调整）。

## 工作流程（逻辑顺序）

1. 将各组审计结果规范为排序引擎输入，生成 `sorted_results` 与人工复核标记。
2. `ConflictResolver` 检测分数差、语义与级别冲突，必要时调用 DeepSeek 裁决，并做证据引用校验与强制证据关联。
3. `ReflectionOrchestrator` 结合规则字典与 `agent_audits` 等记录计算 `initial_score`、`final_score`（含冲突惩罚、证据调整、与 Agent 分 hint 的混合等）。
4. 使用 `src/common/thesis_grade_verdict.py` 生成与四档指标一致的 `verdict`；可选 `DialogueEngine` 生成导师评语；`MarkdownReportGenerator` 写报告文件。

## 架构要点

- 加权权重示例：逻辑 1.2、实验 1.1、文献 1.0、格式 0.8（与 `conflict_resolver.py` 中配置一致）。
- 支持 CLI `run.py`（文件/数据库模式）与 HTTP `main.py` 两种入口；生产联调以 **HTTP + 调度器** 为主。

## 启动与配置

```env
DEEPSEEK_API_KEY=your_api_key_here
# 部分路径另需 LITELLM_API_KEY 或框架映射，以 .env 示例为准
```

```bash
pip install -r requirements.txt
python main.py
```

更细的模块说明、验证清单见 `docs/` 目录；历史合并说明见 `README1.md`（索引向）。
