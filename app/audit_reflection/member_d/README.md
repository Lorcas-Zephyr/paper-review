# 反思组 Member D（裁决与报告）

## 角色

承载 **冲突裁决**、**最终评分合成**、**Markdown 报告生成** 等“主笔”能力：对接 DeepSeek 做高阶推理，输出 `resolved_issues`、`final_verdict` 及报告模板填充所需结构。

## 在架构中的位置

实现集中于 `audit_reflection/src/conflict_resolution/`、`src/common/report_generator.py` 等；对外由 **`main.py` HTTP 服务** 与 **`run.py` CLI** 统一暴露，本目录为历史分包文档位。

## 工作流中的环节

在排序与证据过滤之后：计算加权分、应用冲突惩罚与证据调整、生成四档结论文本、可选导师对话与磁盘报告。

## 使用说明

日常运维只需启动上级 `audit_reflection` 根目录服务；本目录无需单独进程。API Key 使用 `.env` 中的 DeepSeek 相关变量。
