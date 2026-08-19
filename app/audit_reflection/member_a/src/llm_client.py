import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

from litellm import acompletion

from .schemas import ConflictResolutionRequest

logger = logging.getLogger(__name__)

# 项目根目录（用于定位prompt文件）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LLMClient:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.model = self.config.get("model", os.getenv("LLM_MODEL", "deepseek/deepseek-chat"))
        self.api_key = self.config.get("api_key", os.getenv("LLM_API_KEY"))
        self.base_url = self.config.get("base_url", os.getenv("LLM_BASE_URL"))

        # 设置LiteLLM路由
        if self.base_url:
            os.environ[f"{self.model.split('/')[0]}_api_base"] = self.base_url
        if self.api_key:
            os.environ[f"{self.model.split('/')[0]}_api_key"] = self.api_key

    async def resolve_conflicts_with_llm(
            self,
            request: ConflictResolutionRequest,
            conflict_pairs: list,
            paper_context: str
    ) -> tuple[dict, dict]:
        """使用LLM-as-a-judge解决冲突"""
        start_time = time.time()

        # 读取Prompt模板
        system_prompt = self._load_prompt("prompts/conflict_resolution/system_prompt.txt")
        user_prompt_template = self._load_prompt("prompts/conflict_resolution/user_prompt.txt")

        # 构建冲突描述
        conflict_descriptions = []
        for pair in conflict_pairs:
            conflict_descriptions.append(
                f"- {pair['agent1']} (评分:{pair.get('score1', 'N/A')}, 级别:{pair.get('level1', 'N/A')}) "
                f"vs {pair['agent2']} (评分:{pair.get('score2', 'N/A')}, 级别:{pair.get('level2', 'N/A')}): "
                f"'{pair['comment1']}' vs '{pair['comment2']}'"
            )

        # Build user prompt via explicit token replacement to avoid format() KeyError on JSON braces
        user_prompt = self._render_user_prompt(
            template=user_prompt_template,
            paper_title=request.metadata.get("paper_title", "Unknown Paper"),
            paper_context=paper_context[:1000] + "..." if len(paper_context) > 1000 else paper_context,
            conflicts="\n".join(conflict_descriptions),
            agent_names=", ".join(set(
                [p['agent1'] for p in conflict_pairs] + [p['agent2'] for p in conflict_pairs]
            ))
        )

        # 调用LLM（使用acompletion异步调用）
        try:
            response = await acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=request.config.get("temperature", 0.3),
                max_tokens=request.config.get("max_tokens", 1000),
                response_format={"type": "json_object"},
                api_key=self.api_key,
                base_url=self.base_url,
            )

            # 解析响应
            resolution_data = json.loads(response.choices[0].message.content)
            usage = {
                "tokens": response.usage.total_tokens,
                "latency_ms": int((time.time() - start_time) * 1000)
            }

            logger.info("LLM冲突裁决完成, tokens=%d, latency=%dms",
                         usage["tokens"], usage["latency_ms"])
            return resolution_data, usage

        except Exception as e:
            logger.error("LLM调用失败: %s, 使用降级方案", str(e))
            return self._fallback_resolution(conflict_pairs), {
                "tokens": 0,
                "latency_ms": int((time.time() - start_time) * 1000)
            }

    def _load_prompt(self, relative_path: str) -> str:
        """加载Prompt模板（基于项目根目录解析路径）"""
        prompt_path = PROJECT_ROOT / relative_path
        try:
            return prompt_path.read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            logger.warning("Prompt文件未找到: %s, 使用内置默认模板", prompt_path)
            if "system" in relative_path:
                return (
                    "你是一名学术评审专家，需要解决不同评审员之间的意见冲突。"
                    "根据论文上下文和冲突描述，提供客观、平衡的最终意见。"
                    "输出严格遵循JSON格式，包含conflicts_resolved（布尔值）、"
                    "resolved_issues（冲突解决详情列表）和confidence_score（0-1置信度）。"
                )
            else:
                return (
                    "论文标题：{paper_title}\n\n"
                    "论文上下文摘要：{paper_context}\n\n"
                    "检测到以下评审冲突：\n{conflicts}\n\n"
                    "参与评审的Agent: {agent_names}\n\n"
                    "请分析这些冲突，提供客观的最终结论。对于每个冲突：\n"
                    "1. 判断冲突类型（直接矛盾/上下文依赖/度量标准不同/评估范围不一致）\n"
                    "2. 给出解决依据\n"
                    "3. 提供最终评语和建议\n"
                    "4. 确定最终严重级别（Info/Warning/Critical）\n\n"
                    "要求：\n"
                    "- 保持学术严谨性，避免主观臆断\n"
                    "- 引用论文具体内容作为依据\n"
                    "- 如果信息不足，承认局限性并建议人工复核"
                )


    @staticmethod
    def _render_user_prompt(
            template: str,
            paper_title: str,
            paper_context: str,
            conflicts: str,
            agent_names: str
    ) -> str:
        """Render prompt placeholders while keeping non-placeholder braces untouched."""
        replacements = {
            "{paper_title}": paper_title,
            "{paper_context}": paper_context,
            "{conflicts}": conflicts,
            "{agent_names}": agent_names,
        }

        rendered = template
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)
        return rendered

    def _fallback_resolution(self, conflict_pairs: list) -> dict:
        """降级方案：当LLM调用失败时使用规则引擎"""
        resolved_issues = []
        for pair in conflict_pairs:
            # 基于分数差异和级别差异进行简单裁决
            score1 = pair.get('score1', 70)
            score2 = pair.get('score2', 70)
            level1 = pair.get('level1', 'Info')
            level2 = pair.get('level2', 'Info')

            # 优先采信级别更高（更严格）的意见
            level_priority = {'Info': 0, 'Warning': 1, 'Critical': 2}
            if level_priority.get(level1, 0) >= level_priority.get(level2, 0):
                dominant_comment = pair['comment1']
                dominant_agent = pair['agent1']
                final_level = level1
            else:
                dominant_comment = pair['comment2']
                dominant_agent = pair['agent2']
                final_level = level2

            # conflict_type可能是枚举对象，统一转为字符串
            conflict_type = pair.get('conflict_type', 'direct_contradiction')
            if hasattr(conflict_type, 'value'):
                conflict_type = conflict_type.value

            resolved_issues.append({
                "agent1_name": pair['agent1'],
                "agent2_name": pair['agent2'],
                "conflict_type": conflict_type,
                "root_cause": "评审维度差异导致意见分歧",
                "evidence_strength": 0.5,
                "confidence": 0.5,
                "resolved_comment": f"降级裁决（LLM不可用）：倾向采信{dominant_agent}的意见 - {dominant_comment}",
                "resolved_suggestion": "建议人工评审确认最终结论",
                "final_level": final_level,
                "needs_human_review": True,
                "score": (score1 + score2) // 2
            })

        return {
            "conflicts_resolved": len(resolved_issues) > 0,
            "resolved_issues": resolved_issues,
            "confidence_score": 0.5
        }
