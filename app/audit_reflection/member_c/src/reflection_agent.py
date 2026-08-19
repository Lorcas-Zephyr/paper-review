import uuid
from typing import Dict, Any
from src.database import Database
from src.hallucination_engine import HallucinationEngine
from src.dialogue_engine import DialogueEngine
from src.evidence_validator import EvidenceValidator
from src.models import ReflectionTask, ReflectionResult, PrioritizedIssue
from src.config import settings
import logging

logger = logging.getLogger(__name__)

class ReflectionJudgeAgent:
    def __init__(self, db: Database):
        self.db = db
        self.evidence_validator = EvidenceValidator()
        self.hallucination_engine = HallucinationEngine(self.evidence_validator)
        self.dialogue_engine = DialogueEngine()

    async def process_reflection_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        task = ReflectionTask(**task_data)
        paper_id = task.metadata.get('paper_id')
        logger.info(f"Processing reflection for paper {paper_id}")

        # 1. 获取所有 Agent 结果和论文内容
        agent_results = await self.db.get_all_agent_results(paper_id)
        paper_sections = await self.db.get_paper_content(paper_id)

        # 2. 执行反思逻辑
        contradictions = await self.hallucination_engine.check_consistency(agent_results)
        validated_results = await self.hallucination_engine.filter_hallucinations(agent_results, paper_sections)
        resolved_results = self.hallucination_engine.resolve_conflicts(validated_results, contradictions)
        final_score = self.hallucination_engine.calculate_final_score(resolved_results)
        issues = self.hallucination_engine.prioritize_issues(resolved_results)
        needs_review, review_reason = self.hallucination_engine.determine_human_review(issues, final_score)

        # 3. 若需要人工复核，生成导师对话
        mentor_dialogue = None
        if needs_review:
            # 提取论文领域（模拟，实际可从元数据获取）
            field = task.metadata.get('field', '软件工程')
            # 构建 PrioritizedIssue 列表
            prioritized_issues = [
                PrioritizedIssue(
                    description=i['description'],
                    priority=i['priority'],
                    agents=i['agents']
                ) for i in issues
            ]
            mentor_dialogue = await self.dialogue_engine.generate_dialogue(field, prioritized_issues)

        # 4. 生成最终报告（可调用 LLM，此处简化）
        verdict = "通过" if final_score >= 80 else "需要修改后重审" if final_score >= 60 else "拒绝"
        result = ReflectionResult(
            id=str(uuid.uuid4()),
            paper_id=paper_id,
            final_score=final_score,
            verdict=verdict,
            critical_issues=[PrioritizedIssue(**i) for i in issues if i['priority']=='critical'],
            major_issues=[PrioritizedIssue(**i) for i in issues if i['priority']=='major'],
            minor_issues=[PrioritizedIssue(**i) for i in issues if i['priority']=='minor'],
            needs_human_review=needs_review,
            human_review_reason=review_reason,
            mentor_dialogue=mentor_dialogue
        )

        # 5. 保存结果
        await self.db.save_reflection_result(paper_id, result)

        # 6. 返回
        return {
            "request_id": task.request_id,
            "status": "success",
            "result": result.dict()
        }
