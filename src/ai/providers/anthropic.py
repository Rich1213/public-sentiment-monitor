"""
src/ai/providers/anthropic.py — Anthropic Claude Provider

環境變數：
  ANTHROPIC_API_KEY   — API 金鑰
  MODEL_ANTHROPIC_NAME — 模型名稱（預設 claude-3-5-haiku-20241022）
"""
import os
from src.ai.base import ModelProvider


class AnthropicProvider(ModelProvider):
    DEFAULT_MODEL = "claude-3-5-haiku-20241022"

    def __init__(self, api_key: str = None, model: str = None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model or os.getenv("MODEL_ANTHROPIC_NAME", self.DEFAULT_MODEL)

        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY 未設定")

        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        except ImportError:
            raise ImportError("請安裝 anthropic 套件：pip install anthropic>=0.25.0")

    def complete(self, system: str, user: str, **kwargs) -> str:
        max_tokens = kwargs.get("max_tokens", 2048)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system or "You are a helpful assistant.",
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text.strip()

    def name(self) -> str:
        return f"anthropic/{self._model}"
