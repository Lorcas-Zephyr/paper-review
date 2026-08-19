1. 项目负责人的说明：

各位组长 本周任务是优化规则评审 我看很多组都没有把之前的规则指标存入数据库中，只是写死在代码里。考虑到大家课多，本次任务也不复杂，我已经给大家把规则都细化了，并且我也把规则都存入数据库了，大家需要做的就是看我给的文档中的规则，使用数据库的规则进行agent评审，不要写死在代码中了，让去读取数据库中的规则，并把相关结果存储到对应表中，如果数据库中的评审规则和大家之前做的评审有较大的出入，就及时和我沟通确认，完成的小组就在群里回复完成，中枢组如果审计结果表与之前的出入较大，及时和我沟通，保留最终的一版结果，也和各个小组积极对接，保证各个小组api测试成功。

我一共新建了四个表 main_rules是规则总表，rule_judge是属性判定表，agent_audit_result是审计结果表，reflect_agent_verdict是反思评估结果表

main_rules字段说明：

| column\_name    | data\_type                  | udt\_name | udt\_schema | is\_nullable | column\_default    | column\_comment                   |
| :-------------- | :-------------------------- | :-------- | :---------- | :----------- | :----------------- | :-------------------------------- |
| rule\_id        | character varying           | varchar   | pg\_catalog | NO           | null               | 规则唯一标识（如FMT-001/EXP-001） |
| agent\_code     | character varying           | varchar   | pg\_catalog | NO           | null               | 归属智能体编码：FMT/REF/EXP/LOG   |
| agent\_name\_en | character varying           | varchar   | pg\_catalog | NO           | null               | 归属智能体名称（英文）            |
| agent\_name\_cn | character varying           | varchar   | pg\_catalog | NO           | null               | 归属智能体名称（中文）            |
| rule\_name\_en  | character varying           | varchar   | pg\_catalog | NO           | null               | 规则名称（英文）                  |
| rule\_name\_cn  | character varying           | varchar   | pg\_catalog | NO           | null               | 规则名称（中文）                  |
| rule\_detail    | text                        | text      | pg\_catalog | NO           | null               | 规则详细描述（中文）              |
| full\_score     | smallint                    | int2      | pg\_catalog | NO           | null               | 单条规则满分值                    |
| severity        | character varying           | varchar   | pg\_catalog | NO           | null               | 违规等级：CRITICAL/WARNING        |
| rule\_type      | character varying           | varchar   | pg\_catalog | NO           | null               | 规则类型：QUANTITATIVE/BOOLEAN    |
| create\_time    | timestamp without time zone | timestamp | pg\_catalog | YES          | CURRENT\_TIMESTAMP | 创建时间                          |
| update\_time    | timestamp without time zone | timestamp | pg\_catalog | YES          | CURRENT\_TIMESTAMP | 更新时间                          |

main_rules数据：

| rule\_id | agent\_code | agent\_name\_en      | agent\_name\_cn | rule\_name\_en            | rule\_name\_cn           | rule\_detail                                                 | full\_score | severity | rule\_type   | create\_time               | update\_time               |
| :------- | :---------- | :------------------- | :-------------- | :------------------------ | :----------------------- | :----------------------------------------------------------- | :---------- | :------- | :----------- | :------------------------- | :------------------------- |
| FMT-001  | FMT         | FormatAuditAgent     | 格式审计智能体  | PaperTotalWordCount       | 论文总字数达标           | 总字数（不含参考文献/附录）≥30000字（软件工程硕士体量要求）  | 7           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| FMT-002  | FMT         | FormatAuditAgent     | 格式审计智能体  | CoreChapterWordRate       | 核心章节字数占比达标     | 第3-5章原创研究章节字数占比≥60%（保证实质工作量）            | 6           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| FMT-003  | FMT         | FormatAuditAgent     | 格式审计智能体  | TypesettingStandard       | 排版自闭环规范           | 各章另起一页、目录页码与正文严格对应（误差率0）、序号层级符合五级标准 | 3           | WARNING  | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| FMT-004  | FMT         | FormatAuditAgent     | 格式审计智能体  | ChartFormulaStandard      | 图表公式引用/格式规范    | 所有图/表/公式均有正文显式引用，编号按章节编码，公式变量统一斜体 | 4           | WARNING  | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| REF-001  | REF         | LiteratureAuditAgent | 文献审计智能体  | RefTotalCount             | 参考文献总数达标         | 参考文献总数≥60篇（软件工程硕士核心要求）                    | 6           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| REF-002  | REF         | LiteratureAuditAgent | 文献审计智能体  | Recent3YRefRate           | 近3年文献占比达标        | 近3年发表的参考文献占比≥70%（保证研究时效性）                | 5           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| REF-003  | REF         | LiteratureAuditAgent | 文献审计智能体  | TopicHotDifficult         | 选题贴合领域热点/难点    | 绪论明确论证选题为当前领域研究热点或尚未解决的公认难点，且有文献支撑 | 5           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| REF-004  | REF         | LiteratureAuditAgent | 文献审计智能体  | EnglishCCFRefRate         | 英文/CCF文献占比达标     | 英文文献≥30% 或 CCF A/B/C类会议/期刊文献≥20%（保证文献档次） | 4           | WARNING  | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-001  | EXP         | ExperimentDataAgent  | 实验数据智能体  | ExperimentPValueReport    | 必须报告显著性P值        | 论文若宣称显著提升，必须报告P值并说明检验方法（P值≤0.05）    | 6           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-002  | EXP         | ExperimentDataAgent  | 实验数据智能体  | MultiGroupTestRequired    | 多组比较需要检验方法     | 多组实验对比应使用T-test或Wilcoxon检验，不得仅给均值结果     | 4           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-003  | EXP         | ExperimentDataAgent  | 实验数据智能体  | SmallSampleNormalityTest  | 小样本需正态性检验       | 当样本量N&lt;30时，应先进行Shapiro-Wilk正态性检验            | 3           | WARNING  | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-004  | EXP         | ExperimentDataAgent  | 实验数据智能体  | MeanRequiresDispersion    | 均值必须配STD/SEM        | 只报告Mean而不报告STD/SEM视为误差报告不完整                  | 3           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-005  | EXP         | ExperimentDataAgent  | 实验数据智能体  | ChartErrorBarRequired     | 图表应包含误差棒         | 图表若展示均值比较，应提供误差棒或不确定性范围               | 3           | WARNING  | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-006  | EXP         | ExperimentDataAgent  | 实验数据智能体  | TextChartValueConsistency | 正文与图表数值一致       | 正文宣称值必须与图表/表格一致，不一致需标记为高风险          | 4           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-001  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | AbstractFivePart          | 摘要五段式结构完整       | 摘要包含背景/方法/实验/结果/结论五段式，各段核心信息无缺失   | 5           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-002  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | ThreeLevelLogicClosed     | 全文三级逻辑闭环         | 章标题解释总题目、二级标题支撑章标题、段落首句支撑小节标题   | 6           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-003  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | UMLViewCount              | 软件架构UML视图达标      | 含系统实现的论文，需提供≥4种UML视图（用例/类/时序/部署/活动） | 5           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-004  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | CoreTermConsistency       | 全文核心术语一致性       | 算法/架构/核心概念等高频术语命名统一，无同义词混用（如组件/构件） | 4           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-005  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | RelatedTechChapterClosed  | 相关技术章节闭环衔接     | 相关技术篇幅≤全文20%，且每个技术点后有衔接语说明后续应用/改进方式 | 3           | WARNING  | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-006  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | ExperimentAnswerQuestion  | 实验分析回应研究问题     | 实验结果分析需正面回应绪论提出的科学/技术/应用问题，形成研究闭环 | 3           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| LOG-007  | LOG         | LogicAuditAgent      | 逻辑审计智能体  | InnovationPointCount      | 创新点数量达标           | 结论章节明确提炼≥2个实质性创新点，且标注创新点在论文中的具体位置 | 4           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-007  | EXP         | ExperimentDataAgent  | 实验数据智能体  | SotaBaselineMinCount      | 至少对比2种近3年SOTA基线 | 实验必须与至少2种近3年发表的领域SOTA方法在相同数据集、相同评估指标下对比 | 4           | CRITICAL | QUANTITATIVE | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |
| EXP-008  | EXP         | ExperimentDataAgent  | 实验数据智能体  | TrainTestSplitStrictly    | 训练测试严格分离         | 训练集与测试集必须严格划分，禁止数据泄露（如预处理前划分、无交叉污染） | 3           | CRITICAL | BOOLEAN      | 2026-03-17 12:06:54.310040 | 2026-03-17 12:06:54.310040 |

rule_judge字段说明：

| column\_name        | data\_type        | udt\_name | udt\_schema | is\_nullable | column\_default | column\_comment                      |
| :------------------ | :---------------- | :-------- | :---------- | :----------- | :-------------- | :----------------------------------- |
| judge\_id           | character varying | varchar   | pg\_catalog | NO           | null            | 判定属性唯一标识（如JUD-FMT-001）    |
| rule\_id            | character varying | varchar   | pg\_catalog | NO           | null            | 关联主规则表ID                       |
| check\_indicator    | character varying | varchar   | pg\_catalog | YES          | null            | 核查指标名（如total\_word\_count）   |
| operator            | character varying | varchar   | pg\_catalog | YES          | null            | 比较运算符：&gt;/&lt;/&gt;=/&lt;=/== |
| threshold\_val      | character varying | varchar   | pg\_catalog | YES          | null            | 判定阈值（如30000/3/0.05）           |
| threshold\_unit\_en | character varying | varchar   | pg\_catalog | YES          | null            | 阈值单位（英文）：word/count/p-value |
| threshold\_unit\_cn | character varying | varchar   | pg\_catalog | YES          | null            | 阈值单位（中文）：字/个/p值          |
| is\_core\_rule      | smallint          | int2      | pg\_catalog | NO           | 1               | 是否为核心规则：1=是，0=否           |

rule_judge数据：

| judge\_id   | rule\_id | check\_indicator               | operator | threshold\_val | threshold\_unit\_en | threshold\_unit\_cn | is\_core\_rule |
| :---------- | :------- | :----------------------------- | :------- | :------------- | :------------------ | :------------------ | :------------- |
| JUD-FMT-001 | FMT-001  | total\_word\_count             | &gt;=    | 30000          | word                | 字                  | 1              |
| JUD-FMT-002 | FMT-002  | core\_chapter\_rate            | &gt;=    | 60%            | percent             | %                   | 1              |
| JUD-FMT-003 | FMT-003  | typesetting\_standard          | ==       | 1              | bool                | 布尔                | 0              |
| JUD-FMT-004 | FMT-004  | chart\_formula\_standard       | ==       | 1              | bool                | 布尔                | 0              |
| JUD-REF-001 | REF-001  | ref\_total\_count              | &gt;=    | 60             | count               | 个                  | 1              |
| JUD-REF-002 | REF-002  | recent3y\_ref\_rate            | &gt;=    | 70%            | percent             | %                   | 1              |
| JUD-REF-003 | REF-003  | topic\_hot\_difficult          | ==       | 1              | bool                | 布尔                | 1              |
| JUD-REF-004 | REF-004  | english\_ccf\_ref\_rate        | &gt;=    | 30%/20%        | percent             | %                   | 0              |
| JUD-EXP-001 | EXP-001  | p\_value\_max                  | &lt;=    | 0.05           | p-value             | p值                 | 1              |
| JUD-EXP-002 | EXP-002  | multi\_group\_test\_required   | ==       | 1              | bool                | 布尔                | 1              |
| JUD-EXP-003 | EXP-003  | sample\_n\_min\_for\_normality | &gt;=    | 30             | count               | 个                  | 0              |
| JUD-EXP-004 | EXP-004  | mean\_requires\_dispersion     | ==       | 1              | bool                | 布尔                | 1              |
| JUD-EXP-005 | EXP-005  | error\_bar\_required           | ==       | 1              | bool                | 布尔                | 0              |
| JUD-EXP-006 | EXP-006  | text\_chart\_value\_gap        | &lt;=    | 0              | absolute\_gap       | 绝对差值            | 1              |
| JUD-LOG-001 | LOG-001  | abstract\_five\_part           | ==       | 1              | bool                | 布尔                | 1              |
| JUD-LOG-002 | LOG-002  | three\_level\_logic            | ==       | 1              | bool                | 布尔                | 1              |
| JUD-LOG-003 | LOG-003  | uml\_view\_count               | &gt;=    | 4              | count               | 个                  | 1              |
| JUD-LOG-004 | LOG-004  | core\_term\_consistency        | ==       | 1              | bool                | 布尔                | 1              |
| JUD-LOG-005 | LOG-005  | related\_tech\_rate            | &lt;=    | 20%            | percent             | %                   | 0              |
| JUD-LOG-006 | LOG-006  | experiment\_answer\_question   | ==       | 1              | bool                | 布尔                | 1              |
| JUD-LOG-007 | LOG-007  | innovation\_count              | &gt;=    | 2              | count               | 个                  | 1              |
| JUD-EXP-008 | EXP-007  | sota\_baseline\_count          | &gt;=    | 2              | count               | 个                  | 1              |
| JUD-EXP-009 | EXP-008  | data\_leakage\_forbidden       | ==       | 1              | bool                | 布尔                | 1              |

2. 反思评估组代码修改说明：

① 将原先用于传入数据的agent_audits表更改为agent_audit_result表。其字段名、类型等如下表：

| column\_name      | data\_type                  | udt\_name | udt\_schema | is\_nullable | column\_default    | column\_comment                    |
| :---------------- | :-------------------------- | :-------- | :---------- | :----------- | :----------------- | :--------------------------------- |
| result\_id        | character varying           | varchar   | pg\_catalog | NO           | null               | 结果唯一标识（如RES-FMT-P001-001） |
| paper\_id         | character varying           | varchar   | pg\_catalog | NO           | null               | 论文唯一ID（自定义生成，如P001）   |
| paper\_name       | character varying           | varchar   | pg\_catalog | YES          | null               | 论文题目                           |
| agent\_code       | character varying           | varchar   | pg\_catalog | NO           | null               | 智能体编码：FMT/REF/EXP/LOG        |
| rule\_id          | character varying           | varchar   | pg\_catalog | NO           | null               | 关联规则ID                         |
| is\_compliant     | smallint                    | int2      | pg\_catalog | YES          | null               | 是否合规：1=是，0=否               |
| actual\_value     | character varying           | varchar   | pg\_catalog | YES          | null               | 实际核查值（如32000字/0.03/3个）   |
| score\_obtained   | smallint                    | int2      | pg\_catalog | YES          | 0                  | 规则实际得分                       |
| audit\_suggestion | character varying           | varchar   | pg\_catalog | YES          | null               | 单规则审计建议                     |
| audit\_time       | timestamp without time zone | timestamp | pg\_catalog | YES          | CURRENT\_TIMESTAMP | 审计时间                           |
| result\_json      | jsonb                       | jsonb     | pg\_catalog | NO           | '{}'::jsonb        | 审计组输出JSON字符串               |

其中result\_json字段的格式如下（作为我们反思评估组的输入）：

```json
{
  "agent_code": "EXP",
  "audit_results": [
    {
      "result_id": "item-001",
      "paper_id": "paper-001",
      "point": "统计学显著性检验", // 审核点
      "rule_id": "EXP-006"
      "score": 3,               // 评分 (按照main_rules)
      "level": "Warning",        // 级别: Critical/Warning/Info
      "description": "实验三数据分布不均，未进行正态性检验。",
      "evidence_quote": "原文第4.2节提到：'我们直接采用了T检验...'", // 必须从原文摘录
      "location": {"section": "4.2", "line_start": 45}, // 方便前端跳转，同时用于反思评估组幻觉过滤
      "suggestion": "建议补充Shapiro-Wilk检验。"
    }
  ]
}
```

注意，agent_code包含FMT/REF/EXP/LOG这4个审计agent（分别为格式审计、文献审计、实验数据、逻辑审计，这里不考虑代码审计agent）。

此外，result_json的location可用于幻觉过滤，需查询paper_sections表：

| column\_name     | data\_type        | udt\_name | udt\_schema | is\_nullable | column\_default                                          | column\_comment |
| :--------------- | :---------------- | :-------- | :---------- | :----------- | :------------------------------------------------------- | :-------------- |
| section\_id      | integer           | int4      | pg\_catalog | NO           | nextval\('paper\_sections\_section\_id\_seq'::regclass\) | null            |
| paper\_id        | uuid              | uuid      | pg\_catalog | YES          | null                                                     | null            |
| section\_name    | character varying | varchar   | pg\_catalog | YES          | null                                                     | null            |
| section\_content | text              | text      | pg\_catalog | YES          | null                                                     | null            |
| content\_vector  | USER-DEFINED      | vector    | public      | YES          | null                                                     | null            |

② 将原先database模式用于输出数据的agent_audits表更改为reflect_agent_verdict表。其字段名、类型等如下表：

| column\_name             | data\_type                  | udt\_name | udt\_schema | is\_nullable | column\_default                                              | column\_comment                    |
| :----------------------- | :-------------------------- | :-------- | :---------- | :----------- | :----------------------------------------------------------- | :--------------------------------- |
| verdict\_id              | character varying           | varchar   | pg\_catalog | NO           | null                                                         | 仲裁结果唯一标识（如VER-P001-001） |
| paper\_id                | character varying           | varchar   | pg\_catalog | NO           | null                                                         | 论文唯一ID                         |
| paper\_name              | character varying           | varchar   | pg\_catalog | YES          | null                                                         | 论文题目                           |
| initial\_score           | numeric                     | numeric   | pg\_catalog | NO           | null                                                         | 初始综合得分（4个Agent得分和）     |
| conflict\_resolution     | text                        | text      | pg\_catalog | YES          | null                                                         | 冲突裁决内容（JSON格式存储）       |
| conflict\_penalty        | numeric                     | numeric   | pg\_catalog | YES          | 0.00                                                         | 冲突扣分值                         |
| final\_score             | numeric                     | numeric   | pg\_catalog | NO           | null                                                         | 校准后最终综合得分（100分制）      |
| filtered\_suggestions    | json                        | json      | pg\_catalog | YES          | null                                                         | 去重后审计建议（JSON格式）         |
| prioritized\_suggestions | json                        | json      | pg\_catalog | YES          | null                                                         | 优先级排序后建议（JSON格式）       |
| final\_verdict           | character varying           | varchar   | pg\_catalog | NO           | null                                                         | 最终定性结论                       |
| verdict\_tags            | character varying           | varchar   | pg\_catalog | YES          | 'Executive\_Summary,Critical\_Fix\_List,Score\_Calibration'::character varying | 结果标签                           |
| verdict\_time            | timestamp without time zone | timestamp | pg\_catalog | YES          | CURRENT\_TIMESTAMP                                           | 仲裁时间                           |

原有的报告输出仍然保留。

③ 整体逻辑为agent_audit_result表中同一paper_id同时有4个审计组的元组时（1个审计组结果可以有多行），执行database模式命令行，若该paper_id没有被反思评估过，将自动对该paper进行反思评估；否则不执行反思评估。若agent_audit_result表对同一paper_id有更新（时间戳在先前生成的反思评估结果reflect_agent_verdict之前），则重新对该paper_id执行反思评估。

此外增加命令行参数，对指定paper_id的论文进行反思评估，此状态下忽略可能有的4个审计组未全部包括（但要在终端输出提醒，并在final\_verdict字段内说明）。
