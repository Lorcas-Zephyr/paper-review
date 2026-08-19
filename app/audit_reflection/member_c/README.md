# 反思组 Member C（规则与策略）

## 角色

从数据库加载 **main_rules / rule_judge** 等评审规则与阈值，为冲突裁决、扣分与严重度判断提供**可配置依据**，避免完全依赖模型内置先验。

## 在架构中的位置

规则数据经 `EvaluatorManager.get_rules_dict()` 等路径注入 **ReflectionOrchestrator**；与逻辑、格式等 Agent 侧读取的 `main_rules` 属于同一套指标体系的不同消费端。

## 工作流中的环节

每次评估前或缓存失效时拉取规则；与冲突解决、PrioritizedIssue 构建及数据库写回 `reflect_agent_verdict` 相关联。

## 使用说明

调整扣分或严重度时，应优先在数据库规则表中修改，而非硬编码；修改后重启服务或清除缓存使反思侧生效。
