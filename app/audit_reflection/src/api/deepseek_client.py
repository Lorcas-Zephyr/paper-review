"""
统一API配置模块
使用DeepSeek API进行LLM调用
"""
import os
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)

# 尝试加载.env文件
try:
    from dotenv import load_dotenv
    # 查找.env文件（从当前文件向上查找到项目根目录）
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"已加载.env文件: {env_path}")
    else:
        # 尝试从当前工作目录加载
        load_dotenv()
except ImportError:
    logger.warning("未安装python-dotenv库，无法自动加载.env文件")
except Exception as e:
    logger.warning(f"加载.env文件失败: {e}")

# DeepSeek API配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"


class DeepSeekClient:
    """DeepSeek API客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or DEEPSEEK_API_KEY
        self.base_url = base_url or DEEPSEEK_BASE_URL
        self.model = model or DEEPSEEK_MODEL
        self._client: Optional[httpx.AsyncClient] = None

        if not self.api_key:
            logger.warning("DeepSeek API密钥未设置，请设置环境变量DEEPSEEK_API_KEY")

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建HTTP客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=600.0)
        return self._client

    async def close(self):
        """关闭HTTP客户端"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        """支持异步上下文管理器"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时自动关闭客户端"""
        await self.close()

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs
    ) -> Dict[str, Any]:
        """
        调用DeepSeek Chat Completion API

        Args:
            messages: 消息列表，格式为[{"role": "user", "content": "..."}]
            temperature: 温度参数
            max_tokens: 最大token数
            **kwargs: 其他参数

        Returns:
            API响应结果
        """
        if not self.api_key:
            raise ValueError("DeepSeek API密钥未设置")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs
        }

        try:
            client = await self._get_client()
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()

            logger.info(f"DeepSeek API调用成功，使用tokens: {result.get('usage', {})}")
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"DeepSeek API调用失败: HTTP {e.response.status_code}")
            logger.error(f"响应内容: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"DeepSeek API调用异常: {e}")
            raise

    async def resolve_conflicts(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3
    ) -> str:
        """
        使用DeepSeek进行冲突裁决

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度参数

        Returns:
            LLM生成的裁决结果
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            result = await self.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=2000
            )

            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})

            logger.info(f"冲突裁决完成，tokens使用: {usage}")
            return content

        except Exception as e:
            logger.error(f"冲突裁决失败: {e}")
            raise

    async def generate_dialogue(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7
    ) -> str:
        """
        使用DeepSeek生成导师对话

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度参数（对话生成使用较高温度）

        Returns:
            生成的对话内容
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            result = await self.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=1500
            )

            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})

            logger.info(f"对话生成完成，tokens使用: {usage}")
            return content

        except Exception as e:
            logger.error(f"对话生成失败: {e}")
            raise


# 全局DeepSeek客户端实例
deepseek_client = DeepSeekClient()
