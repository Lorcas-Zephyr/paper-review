"""
Markdown报告生成模块
生成符合导师审阅习惯的评审报告
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


class MarkdownReportGenerator:
    """Markdown报告生成器"""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(
        self,
        paper_id: str,
        paper_title: str,
        result: Dict[str, Any]
    ) -> str:
        """
        生成完整的Markdown评审报告

        Args:
            paper_id: 论文ID
            paper_title: 论文标题
            result: 反思评估结果

        Returns:
            报告文件路径
        """
        # 生成报告内容
        content = self._build_report_content(paper_id, paper_title, result)

        # 保存到文件
        filename = f"review_report_{paper_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        filepath = self.output_dir / filename

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

            # 使用绝对路径，便于用户查找
            abs_path = str(filepath.absolute())
            logger.info(f"Markdown报告已生成: {abs_path}")
            return abs_path
        except Exception as e:
            logger.error(f"保存Markdown报告失败: {e}")
            raise

    def _get_metadata(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """获取metadata，兼容metadata和plugin_metadata两种字段名"""
        return result.get("metadata") or result.get("plugin_metadata", {})

    def _build_report_content(
        self,
        paper_id: str,
        paper_title: str,
        result: Dict[str, Any]
    ) -> str:
        """构建报告内容"""
        lines = []

        # 标题和基本信息
        lines.extend(self._build_header(paper_id, paper_title, result))

        # 执行摘要
        lines.extend(self._build_executive_summary(result))

        # 评审结果详情
        lines.extend(self._build_audit_details(result))

        # 冲突裁决说明
        lines.extend(self._build_conflict_resolution(result))

        # 证据验证结果
        lines.extend(self._build_evidence_validation(result))

        # 问题分级列表
        lines.extend(self._build_issues_by_level(result))

        # 导师指导意见
        lines.extend(self._build_mentor_dialogue(result))

        # 人工复核建议
        lines.extend(self._build_human_review_section(result))

        # 最终建议
        lines.extend(self._build_final_recommendation(result))

        # 附录
        lines.extend(self._build_appendix(result))

        # 页脚
        lines.extend(self._build_footer())

        return "\n".join(lines)

    def _build_header(
        self,
        paper_id: str,
        paper_title: str,
        result: Dict[str, Any]
    ) -> List[str]:
        """构建报告头部"""
        return [
            "# 硕士学位论文评审报告",
            "",
            "---",
            "",
            f"**论文编号**: {paper_id}",
            f"**论文标题**: {paper_title}",
            f"**评审日期**: {datetime.now().strftime('%Y年%m月%d日')}",
            f"**评审系统**: 反思评估组 v1.0",
            "",
            "---",
            ""
        ]

    def _build_executive_summary(self, result: Dict[str, Any]) -> List[str]:
        """构建执行摘要"""
        final_score = result.get("final_score", 0)
        verdict = result.get("verdict", "待定")
        critical_count = len(result.get("critical_issues", []))
        major_count = len(result.get("major_issues", []))
        minor_count = len(result.get("minor_issues", []))

        # 确定评级
        if final_score >= 90:
            grade = "优秀 (A)"
        elif final_score >= 80:
            grade = "良好 (B)"
        elif final_score >= 70:
            grade = "中等 (C)"
        elif final_score >= 60:
            grade = "及格 (D)"
        else:
            grade = "不及格 (F)"

        return [
            "## 一、执行摘要",
            "",
            f"### 综合评分: {final_score:.1f} / 100",
            f"### 评审等级: {grade}",
            "",
            f"**评审结论**: {verdict}",
            "",
            "### 问题统计",
            "",
            f"- **关键问题 (Critical)**: {critical_count} 个",
            f"- **重要问题 (Major)**: {major_count} 个",
            f"- **次要问题 (Minor)**: {minor_count} 个",
            f"- **问题总数**: {critical_count + major_count + minor_count} 个",
            "",
            "### 评审组参与情况",
            "",
            "本次评审由5个专业审计组共同完成：",
            "- 格式审计组：论文格式规范性审查",
            "- 逻辑审计组：论证逻辑严密性审查",
            "- 代码审计组：代码质量与规范性审查",
            "- 实验数据组：实验设计与数据分析审查",
            "- 文献真实性组：参考文献真实性核验",
            "",
            "---",
            ""
        ]

    def _build_audit_details(self, result: Dict[str, Any]) -> List[str]:
        """构建审计详情"""
        metadata = self._get_metadata(result)
        conflict_resolution = metadata.get("conflict_resolution", {})
        final_verdict = conflict_resolution.get("final_verdict", {})

        lines = [
            "## 二、各审计组评审详情",
            "",
            "### 2.1 评分分布",
            ""
        ]

        # 如果有原始平均分和调整后平均分
        original_score = final_verdict.get("original_average_score")
        adjusted_score = final_verdict.get("average_score")

        if original_score and adjusted_score:
            lines.append(f"- **原始平均分**: {original_score:.1f}")
            lines.append(f"- **调整后平均分**: {adjusted_score:.1f}")

            if abs(adjusted_score - original_score) > 0.1:
                adjustment = adjusted_score - original_score
                lines.append(f"- **分数调整**: {adjustment:+.1f} (基于证据验证结果)")
        else:
            lines.append(f"- **综合平均分**: {result.get('final_score', 0):.1f}")

        lines.extend([
            "",
            "### 2.2 审计组权重说明",
            "",
            "各审计组根据其专业重要性被赋予不同权重：",
            "- 逻辑审计组: 1.2 (最高权重)",
            "- 代码审计组: 1.1",
            "- 实验数据组: 1.1",
            "- 文献真实性组: 1.0",
            "- 格式审计组: 0.8",
            "",
            "---",
            ""
        ])

        return lines

    def _build_conflict_resolution(self, result: Dict[str, Any]) -> List[str]:
        """构建冲突裁决说明"""
        metadata = self._get_metadata(result)
        conflict_resolution = metadata.get("conflict_resolution", {})
        resolved_issues = conflict_resolution.get("resolved_issues", [])

        lines = [
            "## 三、冲突裁决与加权投票",
            "",
            "### 3.1 冲突检测机制",
            "",
            "系统采用多维度冲突检测机制：",
            "1. **分数差异检测**: 当不同审计组对同一问题的评分差异超过20分时触发",
            "2. **语义冲突检测**: 通过关键词模式匹配检测矛盾性评价（如\"高效\"vs\"低效\"）",
            "3. **级别冲突检测**: 当不同组对同一审核点给出不同问题级别时触发",
            "",
            f"### 3.2 本次评审冲突情况",
            "",
            f"**检测到冲突数量**: {len(resolved_issues)} 个",
            ""
        ]

        if resolved_issues:
            lines.append("### 3.3 冲突裁决详情")
            lines.append("")

            for idx, issue in enumerate(resolved_issues[:5], 1):  # 最多显示5个
                agent1 = issue.get("agent1_name", "未知")
                agent2 = issue.get("agent2_name", "未知")
                conflict_type = issue.get("conflict_type", "未知")
                final_level = issue.get("final_level", "Info")
                resolved_comment = issue.get("resolved_comment", "")
                confidence = issue.get("confidence", 0)

                lines.extend([
                    f"#### 冲突 {idx}: {agent1} vs {agent2}",
                    "",
                    f"- **冲突类型**: {conflict_type}",
                    f"- **裁决级别**: {final_level}",
                    f"- **置信度**: {confidence:.2f}",
                    f"- **裁决意见**: {resolved_comment}",
                    ""
                ])

            if len(resolved_issues) > 5:
                lines.append(f"*（还有 {len(resolved_issues) - 5} 个冲突未在此展示）*")
                lines.append("")

        else:
            lines.append("**本次评审未检测到显著冲突，各审计组意见基本一致。**")
            lines.append("")

        lines.extend([
            "### 3.4 加权投票机制",
            "",
            "当出现冲突时，系统采用以下裁决策略：",
            "1. **权重加权**: 根据审计组权重计算加权平均分",
            "2. **LLM裁决**: 调用DeepSeek主考官Agent进行智能裁决",
            "3. **证据强度**: 优先采信有明确证据支持的意见",
            "4. **专业优先**: 在专业领域内，该领域审计组意见权重更高",
            "",
            "---",
            ""
        ])

        return lines

    def _build_evidence_validation(self, result: Dict[str, Any]) -> List[str]:
        """构建证据验证结果"""
        metadata = self._get_metadata(result)
        conflict_resolution = metadata.get("conflict_resolution", {})
        evidence_validation = conflict_resolution.get("evidence_validation", {})
        evidence_enforcement = conflict_resolution.get("evidence_enforcement", {})

        lines = [
            "## 四、证据真实性验证（幻觉过滤）",
            "",
            "### 4.1 验证机制",
            "",
            "系统对所有审计意见中的证据引用进行真实性验证：",
            "1. **数据源**: 从paper_sections表提取论文原文",
            "2. **匹配方法**: 精确匹配 + 模糊匹配 + 语义匹配",
            "3. **过滤规则**: Warning/Critical级别问题必须包含有效证据，否则视为幻觉并剔除",
            ""
        ]

        if evidence_validation:
            valid_count = evidence_validation.get("valid_count", 0)
            invalid_count = evidence_validation.get("invalid_count", 0)
            total_quotes = evidence_validation.get("total_quotes", 0)
            validation_score = evidence_validation.get("validation_score", 0)

            lines.extend([
                "### 4.2 验证结果",
                "",
                f"- **有效证据引用**: {valid_count} 个",
                f"- **无效证据引用**: {invalid_count} 个",
                f"- **证据总数**: {total_quotes} 个",
                f"- **验证通过率**: {validation_score:.1%}",
                ""
            ])

            if invalid_count > 0:
                invalid_results = evidence_validation.get("invalid_results", [])
                lines.append("### 4.3 无效证据详情")
                lines.append("")
                lines.append("以下证据引用未能在论文原文中找到，已被系统剔除：")
                lines.append("")

                for invalid in invalid_results[:3]:  # 最多显示3个
                    agent_name = invalid.get("agent_name", "未知")
                    quote_preview = invalid.get("clean_quote", "")[:50]
                    lines.append(f"- **{agent_name}**: \"{quote_preview}...\"")

                if len(invalid_results) > 3:
                    lines.append(f"- *（还有 {len(invalid_results) - 3} 个无效引用）*")

                lines.append("")

        if evidence_enforcement:
            removed_count = evidence_enforcement.get("removed_count", 0)
            if removed_count > 0:
                lines.extend([
                    "### 4.4 强制证据关联",
                    "",
                    f"**剔除的无证据问题**: {removed_count} 个",
                    "",
                    "根据\"强制证据关联\"原则，所有Warning/Critical级别的问题必须包含evidence_quote。",
                    "未包含有效证据的问题已被系统自动剔除，以防止AI幻觉。",
                    ""
                ])

        lines.extend([
            "---",
            ""
        ])

        return lines

    def _build_issues_by_level(self, result: Dict[str, Any]) -> List[str]:
        """构建分级问题列表"""
        critical_issues = result.get("critical_issues", [])
        major_issues = result.get("major_issues", [])
        minor_issues = result.get("minor_issues", [])

        lines = [
            "## 五、问题详细列表",
            ""
        ]

        # 关键问题
        if critical_issues:
            lines.extend([
                "### 5.1 关键问题 (Critical) ⚠️",
                "",
                "以下问题严重影响论文质量，必须立即修正：",
                ""
            ])

            for idx, issue in enumerate(critical_issues, 1):
                desc = issue.get("description", "")
                agents = ", ".join(issue.get("agents", []))
                evidence = issue.get("evidence", "")

                lines.extend([
                    f"#### {idx}. {desc}",
                    "",
                    f"- **检测组**: {agents}",
                    f"- **问题根源**: {evidence}",
                    ""
                ])

        # 重要问题
        if major_issues:
            lines.extend([
                "### 5.2 重要问题 (Major) ⚡",
                "",
                "以下问题需要认真对待并进行修改：",
                ""
            ])

            for idx, issue in enumerate(major_issues, 1):
                desc = issue.get("description", "")
                agents = ", ".join(issue.get("agents", []))

                lines.extend([
                    f"#### {idx}. {desc}",
                    "",
                    f"- **检测组**: {agents}",
                    ""
                ])

        # 次要问题
        if minor_issues:
            lines.extend([
                "### 5.3 次要问题 (Minor) ℹ️",
                "",
                "以下问题建议改进以提升论文质量：",
                ""
            ])

            for idx, issue in enumerate(minor_issues[:5], 1):  # 最多显示5个
                desc = issue.get("description", "")
                lines.append(f"{idx}. {desc}")

            if len(minor_issues) > 5:
                lines.append(f"\n*（还有 {len(minor_issues) - 5} 个次要问题未在此展示）*")

            lines.append("")

        # 检查是否全部因为缺乏证据被剔除了
        metadata = self._get_metadata(result)
        conflict_resolution = metadata.get("conflict_resolution", {})
        evidence_enforcement = conflict_resolution.get("evidence_enforcement", {})
        removed_count = evidence_enforcement.get("removed_count", 0)

        if not (critical_issues or major_issues or minor_issues):
            if removed_count > 0:
                lines.append(f"**⚠️ 警告：检测到了潜在问题，但因为均未包含有效的原文证据引用，已被防幻觉机制全部剔除（共剔除了 {removed_count} 个问题）。**")
            else:
                lines.append("**本次评审未发现显著问题，论文质量良好。**")
            lines.append("")

        lines.extend([
            "---",
            ""
        ])

        return lines

    def _build_mentor_dialogue(self, result: Dict[str, Any]) -> List[str]:
        """构建导师指导意见"""
        mentor_dialogue = result.get("mentor_dialogue")

        lines = [
            "## 六、导师指导意见",
            ""
        ]

        if mentor_dialogue:
            role = mentor_dialogue.get("role", "资深导师")
            field = mentor_dialogue.get("field", "软件工程")
            conversation = mentor_dialogue.get("conversation", [])

            lines.extend([
                f"**指导教师**: {role}（{field}领域）",
                "",
                "### 指导对话",
                ""
            ])

            for turn in conversation:
                role = turn.get("role", "mentor")
                content = turn.get("content", "")
                # 将role转换为中文显示
                role_display = "导师" if role == "mentor" else role
                lines.extend([
                    f"**{role_display}**: {content}",
                    ""
                ])

        else:
            lines.append("*本次评审未生成导师指导意见。*")
            lines.append("")

        lines.extend([
            "---",
            ""
        ])

        return lines

    def _build_human_review_section(self, result: Dict[str, Any]) -> List[str]:
        """构建人工复核建议"""
        needs_review = result.get("needs_human_review", False)
        review_reason = result.get("human_review_reason")
        metadata = self._get_metadata(result)
        review_marks_count = metadata.get("review_marks_count", 0)

        lines = [
            "## 七、人工复核建议",
            ""
        ]

        if needs_review:
            lines.extend([
                f"### ⚠️ 建议人工复核",
                "",
                f"**触发原因**: {review_reason}",
                "",
                f"**复核标记数量**: {review_marks_count} 个",
                "",
                "系统检测到以下情况，建议由人工专家进行复核：",
                "- 审计组置信度低于阈值",
                "- 多个审计组意见严重冲突",
                "- 证据缺失或无效",
                "",
                "**复核建议**:",
                "1. 重点关注标记的问题点",
                "2. 核实冲突意见的准确性",
                "3. 补充缺失的证据材料",
                ""
            ])
        else:
            lines.extend([
                "### ✅ 无需人工复核",
                "",
                "系统评审结果置信度高，各审计组意见一致，无需额外人工复核。",
                ""
            ])

        lines.extend([
            "---",
            ""
        ])

        return lines

    def _build_final_recommendation(self, result: Dict[str, Any]) -> List[str]:
        """构建最终建议"""
        verdict = result.get("verdict", "待定")
        final_score = result.get("final_score", 0)

        lines = [
            "## 八、最终评审建议",
            "",
            f"### 综合评分: {final_score:.1f} / 100",
            "",
            f"### 评审结论",
            "",
            f"**{verdict}**",
            "",
            "### 具体建议",
            ""
        ]

        # 根据分数给出具体建议
        if final_score >= 85:
            lines.extend([
                "论文质量优秀，研究工作扎实，论证严密，建议：",
                "1. 直接通过答辩",
                "2. 可作为优秀论文推荐",
                "3. 建议投稿高水平期刊或会议",
                ""
            ])
        elif final_score >= 75:
            lines.extend([
                "论文质量良好，但存在一些可改进之处，建议：",
                "1. 针对指出的问题进行小修",
                "2. 完善实验数据和分析",
                "3. 修改后可通过答辩",
                ""
            ])
        elif final_score >= 60:
            lines.extend([
                "论文基本达到要求，但存在较多问题，建议：",
                "1. 认真修改所有指出的问题",
                "2. 补充必要的实验和论证",
                "3. 大修后重新提交评审",
                ""
            ])
        else:
            lines.extend([
                "论文存在严重问题，未达到硕士学位论文要求，建议：",
                "1. 全面修改论文结构和内容",
                "2. 补充大量实验和数据",
                "3. 重新进行文献调研",
                "4. 延期答辩",
                ""
            ])

        lines.extend([
            "---",
            ""
        ])

        return lines

    def _build_appendix(self, result: Dict[str, Any]) -> List[str]:
        """构建附录"""
        metadata = self._get_metadata(result)

        lines = [
            "## 附录",
            "",
            "### A. 评审系统信息",
            "",
            "- **系统名称**: 反思评估组评审系统",
            "- **系统版本**: v1.0",
            "- **LLM模型**: DeepSeek Chat",
            "- **评审日期**: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "",
            "### B. 评审流程",
            "",
            "1. 5个专业审计组独立评审",
            "2. 优先级排序和复核标记",
            "3. 冲突检测和智能裁决",
            "4. 证据真实性验证（幻觉过滤）",
            "5. 加权投票计算综合得分",
            "6. 生成导师指导意见",
            "7. 输出最终评审报告",
            "",
            "### C. 数据统计",
            ""
        ]

        sorted_results_count = metadata.get("sorted_results_count", 0)
        review_marks_count = metadata.get("review_marks_count", 0)
        conflict_resolution = metadata.get("conflict_resolution", {})
        resolved_issues_count = len(conflict_resolution.get("resolved_issues", []))

        lines.extend([
            f"- **审核项总数**: {sorted_results_count}",
            f"- **复核标记数**: {review_marks_count}",
            f"- **冲突裁决数**: {resolved_issues_count}",
            ""
        ])

        return lines

    def _build_footer(self) -> List[str]:
        """构建页脚"""
        return [
            "---",
            "",
            "*本报告由反思评估组评审系统自动生成*",
            "",
            f"*生成时间: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}*",
            ""
        ]


# 全局实例
report_generator = MarkdownReportGenerator()
