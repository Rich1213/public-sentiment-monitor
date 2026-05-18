"""
src/ai/base.py — ModelProvider 抽象基底類別
"""
from abc import ABC, abstractmethod


class ModelProvider(ABC):
    """所有 LLM provider 的抽象基底類別。"""

    @abstractmethod
    def complete(self, system: str, user: str, **kwargs) -> str:
        """
        呼叫 LLM，回傳純文字回應。

        Args:
            system: system prompt
            user: user prompt
            **kwargs: 額外參數（max_tokens 等）

        Returns:
            LLM 回應文字
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """回傳 provider 識別名稱（用於日誌）。"""
        ...
