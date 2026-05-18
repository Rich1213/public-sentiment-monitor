"""
src/ai/providers/nvidia.py — NVIDIA NIM API Provider

使用 OpenAI 相容介面連接 NVIDIA Inference Microservices。
預設模型：meta/llama-3.3-70b-instruct
"""
import os
from openai import OpenAI
from src.ai.base import ModelProvider


class NvidiaProvider(ModelProvider):
    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
    DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"

    def __init__(self, api_key: str = None, model: str = None, base_url: str = None):
        self._api_key = api_key or os.getenv("NVIDIA_API_KEY", "")
        self._model = model or os.getenv("MODEL_NVIDIA_NAME", self.DEFAULT_MODEL)
        self._base_url = base_url or os.getenv("NVIDIA_BASE_URL", self.DEFAULT_BASE_URL)

        if not self._api_key:
            raise ValueError("NVIDIA_API_KEY 未設定")

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

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
        return f"nvidia/{self._model}"
