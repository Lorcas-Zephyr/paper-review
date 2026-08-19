from google import genai
from google.genai import types
from openai import AsyncOpenAI
from typing import Optional
import json
import re
from utils.logger import setup_logger
from config import (
    GEMINI_MODEL_NAME, GOOGLE_API_KEY,
    QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL_NAME,
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL_NAME,
    DEEPSEEK_THINKING_ENABLED, LLM_PROVIDER, LLM_TIMEOUT_SEC
)
from core.prompts import SYSTEM_PROMPT_MAIN as SYSTEM_PROMPT

logger = setup_logger(__name__)

class LLMClient:
    """
    统一的 LLM 客户端封装，支持 Google Gemini 和 Qwen (DashScope)。
    根据 config.LLM_PROVIDER 动态切换后端。
    """
    def __init__(self):
        self.provider = LLM_PROVIDER.lower() if LLM_PROVIDER else "none"

        # 初始化 Gemini
        if self.provider == "gemini":
            if not GOOGLE_API_KEY:
                self.gemini_client = None
                self.provider = "none"
            else:
                self.gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
            self.model_name = GEMINI_MODEL_NAME

        # 初始化 Qwen (OpenAI Compatible)
        elif self.provider == "qwen":
            if not QWEN_API_KEY:
                self.qwen_client = None
                self.provider = "none"
            else:
                self.qwen_client = AsyncOpenAI(
                    api_key=QWEN_API_KEY,
                    base_url=QWEN_BASE_URL
                )
            self.model_name = QWEN_MODEL_NAME

        # 初始化 DeepSeek (OpenAI Compatible)
        elif self.provider == "deepseek":
            if not DEEPSEEK_API_KEY:
                self.deepseek_client = None
                self.provider = "none"
            else:
                self.deepseek_client = AsyncOpenAI(
                    api_key=DEEPSEEK_API_KEY,
                    base_url=DEEPSEEK_BASE_URL
                )
            self.model_name = DEEPSEEK_MODEL_NAME

        elif self.provider == "mock":
            pass # No client needed

        else:
            self.provider = "none"

    async def scan_document(self, content: str, temperature: float = 0.1) -> str:
        """
        扫描文档内容，执行格式审计。
        """
        if self.provider == "gemini":
            return await self._scan_with_gemini(content, temperature)
        elif self.provider == "qwen":
            return await self._scan_with_qwen(content, temperature)
        elif self.provider == "deepseek":
            return await self._scan_with_deepseek(content, temperature)
        elif self.provider == "mock":
            return await self._scan_with_mock(content, temperature)
        else:
            return ""

    async def _scan_with_mock(self, content: str, temperature: float = 0.1) -> str:
        """
        Mock LLM response for testing purposes.
        """
        import asyncio
        await asyncio.sleep(0.5)
        # Return a valid JSON string simulating issues found by LLM
        # Based on keywords in content to make it semi-realistic
        issues = []

        # Check for terminology issues (simple keyword check)
        if "LLM" in content and "Large Language Model" not in content:
            issues.append({
                "issue_type": "Terminology_Inconsistency",
                "severity": "Info",
                "evidence": "LLM",
                "message": "术语 'LLM' 建议在首次出现时使用全称 'Large Language Model (LLM)'",
                "suggestion": "Large Language Model (LLM)"
            })

        if "e.g." in content:
            issues.append({
                "issue_type": "Abbreviation_Definition",
                "severity": "Info",
                "evidence": "e.g.",
                "message": "建议使用 '例如' 或 'for example' 代替拉丁缩写",
                "suggestion": "例如"
            })

        return json.dumps({"issues": issues, "summary": "Mock LLM scan completed."}, ensure_ascii=False)

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 800,
    ) -> str:
        if self.provider == "mock":
            sys_text = (system_prompt or "").strip()
            user_text = (user_prompt or "").strip()
            if "提取可核查的客观事实点" in sys_text:
                parts = re.split(r"[。！？\n]+", user_text)
                facts = []
                for p in parts:
                    t = p.strip()
                    if not t:
                        continue
                    t = re.sub(r"\s+", " ", t)
                    if len(t) > 30:
                        t = t[:30]
                    facts.append(f"- {t}")
                    if len(facts) >= 12:
                        break
                return "\n".join(facts)

            if "必须以JSON对象输出" in sys_text and "comment" in sys_text and "suggestion" in sys_text:
                issue_summary = ""
                expert_comments = ""
                try:
                    data = json.loads(user_text)
                    issue_summary = str(data.get("issue_summary") or "")
                    expert_comments = str(data.get("expert_comments") or "")
                except Exception:
                    issue_summary = user_text[:800]
                    expert_comments = ""

                top_tip = ""
                m = re.search(r"^- (.+)$", expert_comments, flags=re.MULTILINE)
                if m:
                    top_tip = m.group(1).strip()

                comment = "已完成格式审计。"
                if issue_summary.strip() and issue_summary.strip() != "-":
                    comment = f"发现以下问题：\n{issue_summary}".strip()
                suggestion = "请优先修复 Critical/Warning，再统一排版细节。"
                if top_tip:
                    suggestion = f"{suggestion}\n参考建议：{top_tip}"

                return json.dumps({"comment": comment, "suggestion": suggestion}, ensure_ascii=False)

            return ""

        if self.provider == "gemini":
            if not self.gemini_client:
                return ""
            try:
                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )
                response = await self.gemini_client.aio.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=config,
                )
                return response.text or ""
            except Exception as e:
                logger.error(f"LLM Error (Gemini): {e}", exc_info=True)
                return ""
        elif self.provider == "qwen":
            if not self.qwen_client:
                return ""
            try:
                response = await self.qwen_client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=LLM_TIMEOUT_SEC,
                    extra_body={"thinking": {"type": "enabled" if DEEPSEEK_THINKING_ENABLED else "disabled"}},
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"LLM Error (Qwen): {e}", exc_info=True)
                return ""
        elif self.provider == "deepseek":
            if not self.deepseek_client:
                return ""
            try:
                response = await self.deepseek_client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=LLM_TIMEOUT_SEC,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"LLM Error (DeepSeek): {e}", exc_info=True)
                return ""
        return ""

    async def _scan_with_gemini(self, content: str, temperature: float) -> str:
        if not self.gemini_client:
            return ""

        try:
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=temperature,
                max_output_tokens=8192,
            )

            response = await self.gemini_client.aio.models.generate_content(
                model=self.model_name,
                contents=content,
                config=config
            )
            return response.text
        except Exception as e:
            logger.error(f"LLM Error (Gemini): {e}", exc_info=True)
            return ""

    async def _scan_with_qwen(self, content: str, temperature: float) -> str:
        if not self.qwen_client:
            return ""

        try:
            response = await self.qwen_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content}
                ],
                temperature=temperature,
                max_tokens=2000, # Qwen max output limitation
                timeout=LLM_TIMEOUT_SEC,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM Error (Qwen): {e}", exc_info=True)
            return ""

    async def _scan_with_deepseek(self, content: str, temperature: float) -> str:
        if not self.deepseek_client:
            return ""

        try:
            response = await self.deepseek_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content}
                ],
                temperature=temperature,
                max_tokens=4096, # DeepSeek V3 supports longer context
                timeout=LLM_TIMEOUT_SEC,
                extra_body={"thinking": {"type": "enabled" if DEEPSEEK_THINKING_ENABLED else "disabled"}},
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM Error (DeepSeek): {e}", exc_info=True)
            return ""

# 为了兼容旧代码，保留 GeminiClient 别名，但建议迁移到 LLMClient
GeminiClient = LLMClient
