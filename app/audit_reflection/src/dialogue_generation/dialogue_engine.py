import yaml
from pathlib import Path
from typing import List, Dict, Optional
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.config_c import settings
from src.common.models import MentorDialogue, PrioritizedIssue
from src.api import deepseek_client

logger = logging.getLogger(__name__)

class DialogueEngine:
    def __init__(self):
        self.llm_client = deepseek_client
        self.phrases = self._load_phrases()
        self.role_personas = settings.dialogue.field_personas

    def _load_phrases(self) -> Dict:
        path = Path("config/academic_phrases.yaml")
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
        return {"phrases": {"criticism": {"avoid": [], "replace": []}, "guidance": {"templates": []}}}

    def _build_mentor_persona(self, field: str, severity: str) -> str:
        """根据领域和问题严重性构建导师人设"""
        base = "你是一位{experience}的{field}领域导师，指导风格：{style}"
        experience = self.role_personas.get(field, "资深")
        style = "严谨且直接" if severity == "critical" else "鼓励性引导"
        persona = base.format(experience=experience, field=field, style=style)
        persona += (
            "\n\n【事实约束】分析问题或举例时，只能使用下方「主要问题」及其「依据/证据」中已出现的文字与数字。"
            "禁止编造文中不存在的引用编号、页码或数据；未给出具体编号时勿写「例如正文中出现[58]」这类虚构示例，改用泛指说明即可。"
        )
        return persona

    async def generate_dialogue(self, field: str, issues: List[PrioritizedIssue]) -> MentorDialogue:
        """生成导师对话"""
        # 构建对话上下文
        lines = []
        for i in issues[:3]:
            row = f"- {i.priority}: {i.description}"
            if getattr(i, "evidence", None):
                row += f"（依据/证据：{i.evidence}）"
            lines.append(row)
        issue_descriptions = "\n".join(lines)
        system_prompt = self._build_mentor_persona(field, issues[0].priority if issues else "minor")
        user_prompt = f"""
论文领域：{field}
主要问题：
{issue_descriptions}

请以导师身份，针对上述问题给出指导性对话。对话应包含：
1. 总体评价
2. 具体问题分析与修改建议
3. 鼓励性结尾
请用中文，语气温和但专业，避免直接否定。
"""
        try:
            response = await self.llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1500
            )
            content = response["choices"][0]["message"]["content"]
            # 构建结构化对话（简化：仅返回一条长消息）
            conversation = [{"role": "mentor", "content": content}]
            # 质量自评（简单规则：长度超过100字且包含建议）
            quality = 4.5 if len(content) > 100 and "建议" in content else 3.0
            return MentorDialogue(
                role=settings.dialogue.role,
                field=field,
                conversation=conversation,
                quality_score=quality
            )
        except Exception as e:
            logger.error(f"Dialogue generation failed: {e}")
            # 返回备用对话
            return MentorDialogue(
                role=settings.dialogue.role,
                field=field,
                conversation=[{"role": "mentor", "content": "您的论文有一些可以改进的地方，请参考具体评审意见。"}],
                quality_score=2.0
            )
