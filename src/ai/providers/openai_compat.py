"""
src/ai/providers/openai_compat.py — OpenAI 相容 Provider

支援：
  - OpenAI 官方 API (api.openai.com)
  - 任何 OpenAI-compatible endpoint（Groq、Together、Ollama 等）

環境變數：
  OPENAI_API_KEY     — API 金鑰
  OPENAI_BASE_URL    — 自訂 base URL（選填，預設 OpenAI 官方）
  MODEL_OPENAI_NAME  — 模型名稱（選填）
"""
import os
from openai import OpenAI
from src.ai.base import ModelProvider


class OpenAICompatProvider(ModelProvider):
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: str = None, model: str = None, base_url: str = None):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._model = model or os.getenv("MODEL_OPENAI_NAME", self.DEFAULT_MODEL)
        self._base_url = base_url or os.getenv("OPENAI_BASE_URL", None)

        if not self._api_key:
            raise ValueError("OPENAI_API_KEY 未設定")

        client_kwargs = {"api_key": self._api_key}
        if self._base_url:
            client_kwargs["base_url"] = self._base_url

        self._client = OpenAI(**client_kwargs)

    def complete(self, system: str, user: str, **kwargs) -> str:
        max_tokens = kwargs.get("max_tokens", 2048)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    def name(self) -> str:
        base = self._base_url or "openai"
        return f"openai-compat/{self._model}@{base}"
