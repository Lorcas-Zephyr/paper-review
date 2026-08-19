from typing import List, Dict, Any
from src.models import AgentResult, ContradictionIssue, ProcessedResult, ValidationReport
from src.evidence_validator import EvidenceValidator
import logging

logger = logging.getLogger(__name__)

class HallucinationEngine:
    def __init__(self, evidence_validator: EvidenceValidator):
        self.evidence_validator = evidence_validator
        # Agent 专业权重（示例）
        self.agent_domain_weights = {
            "format": 0.8,
            "logic": 1.0,
            "code": 0.9,
            "experiment": 1.0,
            "literature": 0.7
        }

    async def check_consistency(self, results: List[AgentResult]) -> List[ContradictionIssue]:
        """检查矛盾（简化版：仅根据评语关键词）"""
        contradictions = []
        # 此处可调用 LLM 或规则判断，简单实现
        for i, r1 in enumerate(results):
            for r2 in results[i+1:]:
                # 简单规则：若一个说“好”一个说“差”，则矛盾
                if ("清晰" in r1.result_json.get('comment','') and "不清晰" in r2.result_json.get('comment','')) or \
                   ("不清晰" in r1.result_json.get('comment','') and "清晰" in r2.result_json.get('comment','')):
                    contradictions.append(ContradictionIssue(
                        agents=[r1.agent_name, r2.agent_name],
                        issue=f"矛盾: {r1.result_json.get('comment','')} vs {r2.result_json.get('comment','')}",
                        confidence=0.8
                    ))
        return contradictions

    async def filter_hallucinations(self, results: List[AgentResult], paper_sections: List[Dict]) -> List[ProcessedResult]:
        """验证证据引用是否存在"""
        processed = []
        for res in results:
            evidence_quotes = res.evidence_quotes
            validation_results = []
            for quote in evidence_quotes:
                report = await self.evidence_validator.validate(quote, paper_sections)
                validation_results.append(report)
            # 如果任何关键证据不存在，降低置信度
            confidence = res.confidence
            if any(not v.exists for v in validation_results):
                confidence *= 0.7
            processed.append(ProcessedResult(
                **res.dict(),
                hallucination_details=validation_results,
                confidence=confidence
            ))
        return processed

    def resolve_conflicts(self, results: List[ProcessedResult], contradictions: List[ContradictionIssue]) -> List[ProcessedResult]:
        """冲突仲裁：根据加权模型选择胜出方"""
        # 简单实现：对于矛盾的双方，保留置信度高的结果
        # 这里假设 contradictions 包含了需要仲裁的对
        # 实际可对每个矛盾 issue 进行裁决，标记被否决的结果置信度降低
        # 为简化，我们直接返回原列表（后期扩展）
        return results

    def prioritize_issues(self, results: List[ProcessedResult]) -> List[Dict]:
        """将问题按优先级排序（从 critical 到 minor）"""
        issues = []
        for res in results:
            # 从 result_json 提取问题描述
            comment = res.result_json.get('comment', '')
            priority = res.audit_level
            issues.append({
                'description': comment,
                'priority': priority,
                'agents': [res.agent_name],
                'confidence': res.confidence
            })
        # 按优先级排序：critical > major > minor
        order = {'critical': 0, 'major': 1, 'minor': 2}
        issues.sort(key=lambda x: order.get(x['priority'], 3))
        return issues

    def calculate_final_score(self, results: List[ProcessedResult]) -> float:
        """加权平均计算最终得分（使用置信度加权）"""
        total_weight = sum(r.confidence for r in results)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(r.score * r.confidence for r in results)
        return weighted_sum / total_weight

    def determine_human_review(self, issues: List[Dict], final_score: float) -> tuple[bool, str]:
        """决定哪些项需要人工复核"""
        need = False
        reason = ""
        # 如果存在置信度低的关键问题，或分数低于阈值
        if final_score < 60:
            need = True
            reason = "最终评分过低，建议人工复核"
        for issue in issues:
            if issue['priority'] == 'critical' and issue.get('confidence', 1.0) < 0.6:
                need = True
                reason = f"关键问题置信度低: {issue['description'][:50]}"
                break
        return need, reason
