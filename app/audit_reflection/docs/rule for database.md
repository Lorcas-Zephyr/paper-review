# 智能体专属规则清单

## 通用表头字段说明

| **英文字段名**    | **字段说明**           | **字段类型**             |
|:------------------|:-----------------------|:-------------------------|
| rule_id           | 规则唯一标识           | 字符串                   |
| rule_name_en      | 规则名称（英文）       | 字符串                   |
| rule_name_cn      | 规则名称（中文）       | 字符串                   |
| rule_detail       | 规则详细描述（中文）   | 文本                     |
| full_score        | 单条规则满分值         | 数字（正整数）           |
| severity          | 违规严重等级           | 枚举（CRITICAL/WARNING） |
| check_indicator   | 核查指标名             | 字符串                   |
| operator          | 比较运算符             | 枚举（\>/\</\>=/\<=/==） |
| threshold_val     | 判定阈值               | 字符串 / 数字            |
| threshold_unit_en | 阈值单位（英文）       | 字符串                   |
| threshold_unit_cn | 阈值单位（中文）       | 字符串                   |
| agent_code        | 归属智能体编码         | 枚举（FMT/REF/EXP/LOG）  |
| agent_name_en     | 归属智能体名称（英文） | 字符串                   |
| agent_name_cn     | 归属智能体名称（中文） | 字符串                   |

## 1. 格式审计智能体（FMT）- 满分 20 分

| **rule_id** | **rule_name_en** | **rule_name_cn** | **rule_detail** | **full_score** | **severity** | **check_indicator** | **operator** | **threshold_val** | **threshold_unit_en** | **threshold_unit_cn** | **agent_code** | **agent_name_en** | **agent_name_cn** |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| FMT-001 | PaperTotalWordCount | 论文总字数达标 | 总字数（不含参考文献 / 附录）≥3 万字（软件工程硕士体量要求） | 7 | CRITICAL | total_word_count | ≥ | 30000 | word | 字 | FMT | FormatAuditAgent | 格式审计智能体 |
| FMT-002 | CoreChapterWordRate | 核心章节字数占比达标 | 第 3-5 章原创研究章节字数占比≥60%（保证实质工作量） | 6 | CRITICAL | core_chapter_rate | ≥ | 60% | percent | % | FMT | FormatAuditAgent | 格式审计智能体 |
| FMT-003 | TypesettingStandard | 排版自闭环规范 | 各章另起一页、目录页码与正文严格对应（误差率 0）、序号层级符合五级标准 | 3 | WARNING | typesetting_standard | == | 1 | \- | \- | FMT | FormatAuditAgent | 格式审计智能体 |
| FMT-004 | ChartFormulaStandard | 图表公式引用 / 格式规范 | 所有图 / 表 / 公式均有正文显式引用，编号按章节编码，公式变量统一斜体 | 4 | WARNING | chart_formula_standard | == | 1 | \- | \- | FMT | FormatAuditAgent | 格式审计智能体 |
| **合计** | \- | \- | \- | **20** |  |  |  |  |  |  |  |  |  |

## 2. 文献审计智能体（REF）- 满分 20 分

| **rule_id** | **rule_name_en** | **rule_name_cn** | **rule_detail** | **full_score** | **severity** | **check_indicator** | **operator** | **threshold_val** | **threshold_unit_en** | **threshold_unit_cn** | **agent_code** | **agent_name_en** | **agent_name_cn** |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| REF-001 | RefTotalCount | 参考文献总数达标 | 参考文献总数≥60 篇（软件工程硕士核心要求） | 6 | CRITICAL | ref_total_count | ≥ | 60 | piece | 篇 | REF | LiteratureAuditAgent | 文献审计智能体 |
| REF-002 | Recent3YRefRate | 近 3 年文献占比达标 | 近 3 年发表的参考文献占比≥70%（保证研究时效性） | 5 | CRITICAL | recent3y_ref_rate | ≥ | 70% | percent | % | REF | LiteratureAuditAgent | 文献审计智能体 |
| REF-003 | TopicHotDifficult | 选题贴合领域热点 / 难点 | 绪论明确论证选题为当前领域研究热点或尚未解决的公认难点，且有文献支撑 | 5 | CRITICAL | topic_hot_difficult | == | 1 | \- | \- | REF | LiteratureAuditAgent | 文献审计智能体 |
| REF-004 | EnglishCCFRefRate | 英文 / CCF 文献占比达标 | 英文文献≥30% 或 CCF A/B/C 类会议 / 期刊文献≥20%（保证文献档次） | 4 | WARNING | english_ccf_ref_rate | ≥ | 30%/20% | percent | % | REF | LiteratureAuditAgent | 文献审计智能体 |
| **合计** | \- | \- | \- | **20** |  |  |  |  |  |  |  |  |  |

## 3. 实验数据智能体（EXP）规则清单30

| **rule_id** | **rule_name_en** | **rule_name_cn** | **rule_detail** | **full_score** | **severity** | **check_indicator** | **operator** | **threshold_val** | **threshold_unit_en** | **threshold_unit_cn** | **agent_code** | **agent_name_en** | **agent_name_cn** |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| EXP-001 | ExperimentPValueReport | 必须报告显著性 P 值 | 论文若宣称显著提升，必须报告 P 值并说明检验方法。 | 6 | CRITICAL | p_value_max | ≤ | 0.05 | p-value | p 值 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-002 | MultiGroupTestRequired | 多组比较需要检验方法 | 多组实验对比应使用 T-test 或 Wilcoxon 检验，不得仅给均值结果。 | 4 | CRITICAL | multi_group_test_required | == | 1 | bool | 布尔 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-003 | SmallSampleNormalityTest | 小样本需正态性检验 | 当样本量 N\<30 时，应先进行 Shapiro-Wilk 正态性检验。 | 3 | WARNING | sample_n_min_for_normality_test | \>= | 30 | count | 个 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-004 | MeanRequiresDispersion | 均值必须配 STD/SEM | 只报告 Mean 而不报告 STD/SEM 视为误差报告不完整。 | 3 | CRITICAL | mean_requires_dispersion | == | 1 | bool | 布尔 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-005 | ChartErrorBarRequired | 图表应包含误差棒 | 图表若展示均值比较，应提供误差棒或不确定性范围。 | 3 | WARNING | error_bar_required | == | 1 | bool | 布尔 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-006 | TextChartValueConsistency | 正文与图表数值一致 | 正文宣称值必须与图表 / 表格一致，不一致需标记为高风险。 | 4 | CRITICAL | text_chart_value_gap_max | \<= | 0 | absolute_gap | 绝对差值 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-007 | SotaBaselineMinCount | 实验应至少对比2种近3年SOTA基线 | 实验必须与至少2种近3年发表的领域SOTA方法在相同数据集、相同评估指标下对比 | 4 | CRITICAL | sota_baseline_min_count | \>= | 2 | count | 个 | EXP | ExperimentDataAgent | 实验数据智能体 |
| EXP-008 | TrainTestSplitStrictly | 训练测试严格分离 | 训练集与测试集必须严格划分，禁止数据泄露。 | 3 | CRITICAL | data_leakage_forbidden | == | 1 | bool | 布尔 | EXP | ExperimentDataAgent | 实验数据智能体 |
| **合计** | \- | \- | \- | **30** | \- |  |  |  |  |  |  |  |  |

## 4. 逻辑审计智能体（LOG）- 满分 30 分

| **rule_id** | **rule_name_en** | **rule_name_cn** | **rule_detail** | **full_score** | **severity** | **check_indicator** | **operator** | **threshold_val** | **threshold_unit_en** | **threshold_unit_cn** | **agent_code** | **agent_name_en** | **agent_name_cn** |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| LOG-001 | AbstractFivePart | 摘要五段式结构完整 | 摘要包含背景 / 方法 / 实验 / 结果 / 结论五段式，各段核心信息无缺失 | 5 | CRITICAL | abstract_five_part | == | 1 | \- | \- | LOG | LogicAuditAgent | 逻辑审计智能体 |
| LOG-002 | ThreeLevelLogicClosed | 全文三级逻辑闭环 | 章标题解释总题目、二级标题支撑章标题、段落首句支撑小节标题 | 6 | CRITICAL | three_level_logic | == | 1 | \- | \- | LOG | LogicAuditAgent | 逻辑审计智能体 |
| LOG-003 | UMLViewCount | 软件架构 UML 视图达标 | 含系统实现的论文，需提供≥3 种 UML 视图（用例 / 类 / 时序 / 部署 / 活动） | 5 | CRITICAL | uml_view_count | ≥ | 3 | piece | 种 | LOG | LogicAuditAgent | 逻辑审计智能体 |
| LOG-004 | CoreTermConsistency | 全文核心术语一致性 | 算法 / 架构 / 核心概念等高频术语命名统一，无同义词混用（如组件 / 构件） | 4 | CRITICAL | term_consistency | == | 1 | \- | \- | LOG | LogicAuditAgent | 逻辑审计智能体 |
| LOG-005 | RelatedTechChapterClosed | 相关技术章节闭环衔接 | 相关技术篇幅≤全文 20%，且每个技术点后有衔接语说明后续应用 / 改进方式 | 3 | WARNING | related_tech_rate | ≤ | 20% | percent | % | LOG | LogicAuditAgent | 逻辑审计智能体 |
| LOG-006 | ExperimentAnswerQuestion | 实验分析回应研究问题 | 实验结果分析需正面回应绪论提出的科学 / 技术 / 应用问题，形成研究闭环 | 3 | CRITICAL | experiment_answer_question | == | 1 | \- | \- | LOG | LogicAuditAgent | 逻辑审计智能体 |
| LOG-007 | InnovationPointCount | 创新点数量达标 | 结论章节明确提炼≥2 个实质性创新点，且标注创新点在论文中的具体位置 | 4 | CRITICAL | innovation_count | ≥ | 2 | piece | 个 | LOG | LogicAuditAgent | 逻辑审计智能体 |
| **合计** | \- | \- | \- | 30 |  |  |  |  |  |  |  |  |  |
