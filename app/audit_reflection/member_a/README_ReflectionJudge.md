# 冲突裁决与反思评估（ReflectionJudge）说明

## 定位

描述组内「主审稿」能力：**冲突检测**、**LLM/规则裁决**、**报告生成**与 **FastAPI 服务**。当前**以仓库根目录 `audit_reflection` 为唯一维护源**：实现已合并至 `src/conflict_resolution/conflict_resolver.py`、`main.py`、`run.py` 等，本文件仅作概念索引。

## 架构与工作流（概念）

1. **输入**：各审计组 JSON + 可选论文全文（用于证据匹配）。
2. **冲突检测**：分数差、级别不一致、语义矛盾等。
3. **裁决**：优先 DeepSeek 辅助；失败时规则降级。
4. **输出**：`resolved_issues`、`final_verdict`（含加权分与四档结论文本）、可选 Markdown 报告。

## 请优先阅读

- `../../README.md` — 反思服务启动与端到端位置
- `../../README1.md` — 模块路径索引表
- `../../docs/功能验证清单.md` — 验证步骤

以下历史长文已收敛；若需旧版逐条 API 说明，请从 Git 历史恢复对应版本。
