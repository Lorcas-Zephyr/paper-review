import json
import random
from typing import Dict, Any, List


class MockDataGenerator:
    """生成模拟的Agent结果数据，用于测试反思评估组的冲突裁决功能"""

    def __init__(self):
        self.agent_names = [
            "格式审计组",
            "逻辑审计组",
            "代码审计组",
            "实验数据组",
            "文献真实性组"
        ]

        self.comments_templates = {
            "格式审计组": [
                "引用格式符合IEEE标准",
                "图表编号连续且正确",
                "发现3处中英文标点混用",
                "参考文献列表格式规范",
                "标题层级存在跳级问题"
            ],
            "逻辑审计组": [
                "论文逻辑结构清晰，论证严密",
                "实验结论与数据分析存在矛盾",
                "假设前提未在正文中充分说明",
                "因果关系推导存在跳跃",
                "整体论证过程自洽"
            ],
            "代码审计组": [
                "算法实现高效，时间复杂度为O(n log n)",
                "代码结构清晰，变量命名规范",
                "发现潜在内存泄漏风险",
                "代码注释与实际逻辑不一致",
                "算法实现与论文描述不符"
            ],
            "实验数据组": [
                "实验设计合理，对照组设置恰当",
                "实验结果显示算法在大数据集上运行缓慢",
                "样本量不足，影响结论可靠性",
                "图表数据与正文描述一致",
                "缺乏显著性检验，结论可信度低"
            ],
            "文献真实性组": [
                "参考文献真实可信，来源权威",
                "发现2篇虚假文献引用",
                "部分文献与引用内容相关性低",
                "引用时效性良好，包含最新研究成果",
                "文献综述全面，覆盖领域主要进展"
            ]
        }

        self.suggestions_templates = {
            "格式审计组": [
                "无需修改",
                "建议调整图表位置",
                "请修正标点符号使用",
                "请补充缺失的参考文献条目",
                "请调整标题层级结构"
            ],
            "逻辑审计组": [
                "论证过程良好",
                "建议重新审视实验结论",
                "请补充假设前提的详细说明",
                "建议完善论证链条",
                "保持现有逻辑结构"
            ],
            "代码审计组": [
                "代码质量高，无需修改",
                "建议优化内存管理",
                "请更新代码注释",
                "建议检查算法实现一致性",
                "请修正代码实现错误"
            ],
            "实验数据组": [
                "实验设计完善",
                "建议优化算法性能",
                "请增加样本量",
                "保持现有实验设计",
                "请补充显著性检验"
            ],
            "文献真实性组": [
                "引用规范，无需修改",
                "请核实并替换虚假文献",
                "建议引用更相关的文献",
                "保持现有文献引用",
                "建议补充最新研究成果"
            ]
        }

        self.tags_templates = {
            "格式审计组": ["Citation_Inconsistency", "Label_Missing", "Punctuation_Error", "Hierarchy_Fault"],
            "逻辑审计组": ["Logic_Leap", "Circular_Reasoning", "Contradictory_Claim", "Unsupported_Arg"],
            "代码审计组": ["Syntax_Error", "Logic_Flaw", "Performance_Issue", "Plagiarism_Risk"],
            "实验数据组": ["Statistical_Weakness", "Data_Inconsistency", "Baseline_Missing"],
            "文献真实性组": ["Fake_Reference", "Outdated_Source", "Misinterpretation"]
        }

    def generate_agent_result(self, agent_name: str = None, score: int = None, audit_level: str = None) -> Dict[
        str, Any]:
        """生成单个Agent的模拟结果"""
        if agent_name is None:
            agent_name = random.choice(self.agent_names)

        if score is None:
            # 根据agent_name设置不同的分数范围
            if agent_name == "格式审计组":
                score = random.randint(75, 95)
            elif agent_name == "逻辑审计组":
                score = random.randint(60, 90)
            elif agent_name == "代码审计组":
                score = random.randint(65, 90)
            elif agent_name == "实验数据组":
                score = random.randint(55, 85)
            else:  # 文献真实性组
                score = random.randint(70, 90)

        if audit_level is None:
            # 根据分数确定audit_level
            if score >= 85:
                audit_level = "Info"
            elif score >= 70:
                audit_level = "Warning"
            else:
                audit_level = "Critical"

        comment = random.choice(self.comments_templates[agent_name])
        suggestion = random.choice(self.suggestions_templates[agent_name])
        tags = random.sample(self.tags_templates[agent_name], random.randint(1, 2))

        return {
            "request_id": f"req_{random.randint(1000, 9999)}",
            "agent_info": {
                "name": agent_name,
                "version": "v1.0"
            },
            "result": {
                "score": score,
                "audit_level": audit_level,
                "comment": comment,
                "suggestion": suggestion,
                "tags": tags
            },
            "usage": {
                "tokens": random.randint(50, 200),
                "latency_ms": random.randint(300, 1500)
            }
        }

    def generate_conflicting_pair(self) -> List[Dict[str, Any]]:
        """生成一对有冲突的Agent结果"""
        # 生成代码审计组和实验数据组的冲突结果
        code_agent = self.generate_agent_result(
            agent_name="代码审计组",
            score=85,
            audit_level="Info"
        )
        code_agent["result"]["comment"] = "算法实现高效，时间复杂度为O(n log n)"
        code_agent["result"]["suggestion"] = "代码结构清晰，无明显优化空间"

        data_agent = self.generate_agent_result(
            agent_name="实验数据组",
            score=55,
            audit_level="Warning"
        )
        data_agent["result"]["comment"] = "实验结果显示算法在大数据集上运行缓慢"
        data_agent["result"]["suggestion"] = "建议优化算法实现或考虑替代方案"

        return [code_agent, data_agent]

    def generate_agent_results(self, num_agents: int = 5, with_conflict: bool = False) -> List[Dict[str, Any]]:
        """生成多个Agent的模拟结果"""
        if with_conflict:
            # 生成有冲突的结果对
            results = self.generate_conflicting_pair()

            # 添加其他无冲突的Agent结果
            for _ in range(num_agents - 2):
                agent_name = random.choice([n for n in self.agent_names if n not in ["代码审计组", "实验数据组"]])
                results.append(self.generate_agent_result(agent_name=agent_name))
        else:
            # 生成无冲突的结果
            results = []
            for i in range(num_agents):
                agent_name = self.agent_names[i % len(self.agent_names)]
                results.append(self.generate_agent_result(agent_name=agent_name))

        return results

    def generate_json_string(self, agent_results: List[Dict[str, Any]] = None) -> str:
        """生成JSON格式的Agent结果字符串"""
        if agent_results is None:
            agent_results = self.generate_agent_results(with_conflict=True)

        return json.dumps({
            "agent_results": agent_results
        }, ensure_ascii=False, indent=2)

    def generate_new_format_data(self, group_id: int = 2, num_results: int = 3, with_conflict: bool = False) -> Dict[str, Any]:
        """生成新格式（work_week2.txt）的审计结果数据

        格式:
        {
            "group_id": 2,
            "audit_results": [
                {
                    "id": "item-001",
                    "point": "统计学显著性检验",
                    "score": 85,
                    "level": "Warning",
                    "description": "实验三数据分布不均，未进行正态性检验。",
                    "evidence_quote": "原文第4.2节提到：'我们直接采用了T检验...'",
                    "location": {"section": "4.2", "line_start": 45},
                    "suggestion": "建议补充Shapiro-Wilk检验。"
                }
            ]
        }
        """
        # 组名映射
        group_name_map = {
            2: "格式审计组",
            3: "逻辑审计组",
            4: "代码审计组",
            5: "实验数据组",
            6: "文献真实性组"
        }

        group_name = group_name_map.get(group_id, f"Group_{group_id}")

        # 为新格式创建模板
        point_templates = {
            2: ["引用格式一致性", "图表编号连续性", "标点符号使用", "参考文献格式", "标题层级结构"],
            3: ["逻辑论证严密性", "实验结论一致性", "假设前提说明", "因果关系推导", "论证自洽性"],
            4: ["算法实现效率", "代码结构清晰度", "内存泄漏风险", "代码注释一致性", "算法描述一致性"],
            5: ["实验设计合理性", "算法运行性能", "样本量充足性", "图表数据一致性", "显著性检验完整性"],
            6: ["参考文献真实性", "虚假文献检测", "文献相关性", "引用时效性", "文献综述全面性"]
        }

        description_templates = {
            2: [
                "引用格式符合IEEE标准，但存在少量不一致。",
                "图表编号连续且正确，但图5标题位置不规范。",
                "发现3处中英文标点混用，影响阅读体验。",
                "参考文献列表格式规范，但缺少DOI信息。",
                "标题层级存在跳级问题，从2.1直接跳到2.1.2。"
            ],
            3: [
                "论文逻辑结构清晰，论证严密，但部分推理存在跳跃。",
                "实验结论与数据分析存在矛盾，需要进一步解释。",
                "假设前提未在正文中充分说明，影响论证可信度。",
                "因果关系推导存在跳跃，缺乏中间步骤说明。",
                "整体论证过程自洽，但部分论点缺乏数据支持。"
            ],
            4: [
                "算法实现高效，时间复杂度为O(n log n)，符合论文描述。",
                "代码结构清晰，变量命名规范，但存在冗余代码。",
                "发现潜在内存泄漏风险，建议增加资源释放逻辑。",
                "代码注释与实际逻辑不一致，需要更新注释。",
                "算法实现与论文描述不符，存在参数不一致问题。"
            ],
            5: [
                "实验设计合理，对照组设置恰当，但样本量不足。",
                "实验结果显示算法在大数据集上运行缓慢，需要优化。",
                "样本量不足，影响结论可靠性，建议增加样本量。",
                "图表数据与正文描述一致，但缺乏误差棒显示。",
                "缺乏显著性检验，结论可信度低，建议补充统计检验。"
            ],
            6: [
                "参考文献真实可信，来源权威，但部分文献较旧。",
                "发现2篇虚假文献引用，需要核实并替换。",
                "部分文献与引用内容相关性低，建议选择更相关文献。",
                "引用时效性良好，包含最新研究成果，但缺少经典文献。",
                "文献综述全面，覆盖领域主要进展，但缺乏批判性分析。"
            ]
        }

        evidence_quote_templates = [
            "原文第3.2节提到：'我们采用了基于Transformer的模型进行实验。'",
            "论文第4.1节指出：'实验结果显示准确率达到95%。'",
            "文中第2.3节说明：'该方法的时间复杂度为O(n^2)。'",
            "第5.4节描述：'对比实验结果表明，我们的方法优于基准方法。'",
            "第1.2节提到：'本研究旨在解决深度学习中的过拟合问题。'"
        ]

        suggestion_templates = {
            2: ["无需修改", "建议调整图表位置", "请修正标点符号使用", "请补充缺失的参考文献条目", "请调整标题层级结构"],
            3: ["论证过程良好", "建议重新审视实验结论", "请补充假设前提的详细说明", "建议完善论证链条", "保持现有逻辑结构"],
            4: ["代码质量高，无需修改", "建议优化内存管理", "请更新代码注释", "建议检查算法实现一致性", "请修正代码实现错误"],
            5: ["实验设计完善", "建议优化算法性能", "请增加样本量", "保持现有实验设计", "请补充显著性检验"],
            6: ["引用规范，无需修改", "请核实并替换虚假文献", "建议引用更相关的文献", "保持现有文献引用", "建议补充最新研究成果"]
        }

        audit_results = []
        points = point_templates.get(group_id, point_templates[2])
        descriptions = description_templates.get(group_id, description_templates[2])
        suggestions = suggestion_templates.get(group_id, suggestion_templates[2])

        for i in range(num_results):
            # 如果有冲突，生成矛盾的分数和级别
            if with_conflict and group_id in [4, 5]:  # 代码组和实验组冲突
                if group_id == 4:  # 代码组高分
                    score = random.randint(80, 95)
                    level = "Info" if score >= 85 else "Warning"
                else:  # 实验组低分
                    score = random.randint(55, 70)
                    level = "Warning" if score >= 65 else "Critical"
            else:
                score = random.randint(60, 95)
                if score >= 85:
                    level = "Info"
                elif score >= 70:
                    level = "Warning"
                else:
                    level = "Critical"

            point = random.choice(points)
            description = random.choice(descriptions)
            evidence_quote = random.choice(evidence_quote_templates)
            suggestion = random.choice(suggestions)

            # 生成位置信息
            section = f"{random.randint(1, 5)}.{random.randint(1, 4)}"
            line_start = random.randint(10, 200)

            audit_results.append({
                "id": f"item-{group_id:02d}-{i+1:03d}",
                "point": point,
                "score": score,
                "level": level,
                "description": description,
                "evidence_quote": evidence_quote,
                "location": {"section": section, "line_start": line_start},
                "suggestion": suggestion
            })

        return {
            "group_id": group_id,
            "audit_results": audit_results
        }

    def generate_all_groups_new_format(self, with_conflict: bool = False) -> List[Dict[str, Any]]:
        """生成所有审计组的新格式数据

        返回格式：包含所有组数据的列表，每个元素是新格式的字典
        """
        all_groups_data = []

        # 生成所有组的数据（组ID 2-6）
        for group_id in range(2, 7):
            # 如果有冲突，为代码组和实验组生成矛盾数据
            group_with_conflict = with_conflict and group_id in [4, 5]
            group_data = self.generate_new_format_data(
                group_id=group_id,
                num_results=random.randint(2, 4),
                with_conflict=group_with_conflict
            )
            all_groups_data.append(group_data)

        return all_groups_data

    def generate_conflict_resolution_request(self, paper_title: str = "深度学习模型优化研究",
                                             with_conflict: bool = True) -> Dict[str, Any]:
        """生成完整的冲突裁决请求"""
        agent_results = self.generate_agent_results(with_conflict=with_conflict)

        return {
            "request_id": f"req_{random.randint(10000, 99999)}",
            "metadata": {
                "paper_id": f"paper_{random.randint(1000, 9999)}",
                "paper_title": paper_title
            },
            "payload": {
                "agent_results": agent_results
            },
            "config": {
                "temperature": 0.3,
                "max_tokens": 1000,
                "conflict_threshold": 0.7
            }
        }


if __name__ == "__main__":
    generator = MockDataGenerator()

    # 生成有冲突的Agent结果JSON字符串
    conflict_json = generator.generate_json_string()
    print("=== 有冲突的Agent结果JSON字符串 ===")
    print(conflict_json)

    # 生成无冲突的Agent结果JSON字符串
    no_conflict_json = generator.generate_json_string(generator.generate_agent_results(with_conflict=False))
    print("\n=== 无冲突的Agent结果JSON字符串 ===")
    print(no_conflict_json)

    # 生成完整的冲突裁决请求
    conflict_request = generator.generate_conflict_resolution_request()
    print("\n=== 完整的冲突裁决请求 ===")
    print(json.dumps(conflict_request, ensure_ascii=False, indent=2))
