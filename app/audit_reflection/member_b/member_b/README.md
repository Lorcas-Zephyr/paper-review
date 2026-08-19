# 反思组 Member B（结果预处理）

## 角色

对四个底层 Agent 返回的**异构 JSON** 做扁平化与降噪：统一嵌套的 `audit_results`、补全缺失的顶层分数/描述字段，降低后续冲突裁决与 LLM 输入的结构风险。

## 在架构中的位置

作为 **ReflectionOrchestrator** 流水线中的中间层，在优先级排序与冲突检测之前或并行阶段消费原始 `audit_groups`，不对外提供独立 API。

## 工作流中的环节

调度器将各组结果打包传入反思服务后，首先经规范化与字段映射（与 `review_engine` 的 `field_mapping` 等配置协同），再进入冲突裁决与打分。

## 使用说明

由 `audit_reflection` 主包自动引用；开发时若新增 Agent 字段，需同步更新映射与扁平化逻辑。
