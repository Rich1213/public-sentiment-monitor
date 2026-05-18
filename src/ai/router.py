"""
src/ai/router.py — ModelRouter

根據環境變數將任務路由到對應的 ModelProvider。
支援 fallback：若主 provider 失敗，自動嘗試 fallback provider。

使用範例：
    router = ModelRouter()
    provider = router.get("sentiment")
    result = provider.complete(system="...", user="...")
"""
import os
import logging
from typing import Optional

from src.ai.base import ModelProvider
from src.ai.config import RouterConfig, TaskModelConfig

logger = logging.getLogger(__name__)


def _build_provider(config: TaskModelConfig) -> ModelProvider:
    """根據 TaskModelConfig 實例化對應的 provider。"""
    provider_name = config.provider.lower()

    if provider_name == "nvidia":
        from src.ai.providers.nvidia import NvidiaProvider
        kwargs = {}
        if config.model:
            kwargs["model"] = config.model
        return NvidiaProvider(**kwargs)

    elif provider_name in ("openai", "openai_compat", "openai-compat"):
        from src.ai.providers.openai_compat import OpenAICompatProvider
        kwargs = {}
        if config.model:
            kwargs["model"] = config.model
        return OpenAICompatProvider(**kwargs)

    elif provider_name == "anthropic":
        from src.ai.providers.anthropic import AnthropicProvider
        kwargs = {}
        if config.model:
            kwargs["model"] = config.model
        return AnthropicProvider(**kwargs)

    else:
        raise ValueError(f"未知的 provider：{provider_name}（支援：nvidia / openai / anthropic）")


class ModelRouter:
    """
    將不同任務路由到對應的 LLM provider。

    四種明確任務類型（對應不同模型設定）：
        sentiment   — 情感分析（追求 JSON 格式精確）
        theme       — 主題分類（可與 sentiment 共用模型）
        pr_advisor  — 公關策略（長文，追求推理深度）
        fallback    — 兜底備用（任意任務失敗時自動切換）

    向下相容別名：
        "pr" 自動映射到 "pr_advisor"

    環境變數：
        MODEL_SENTIMENT_PROVIDER  / MODEL_SENTIMENT_NAME
        MODEL_THEME_PROVIDER      / MODEL_THEME_NAME
        MODEL_PR_ADVISOR_PROVIDER / MODEL_PR_ADVISOR_NAME
        MODEL_FALLBACK_PROVIDER   / MODEL_FALLBACK_NAME

    預設全部使用 NVIDIA（維持現有行為）。
    """

    # 向下相容別名（舊程式碼用 "pr"，自動映射到 "pr_advisor"）
    _TASK_ALIASES: dict = {"pr": "pr_advisor"}

    def __init__(self, config: RouterConfig = None):
        self._config = config or RouterConfig.from_env()
        self._cache: dict[str, ModelProvider] = {}

    def get(self, task: str) -> ModelProvider:
        """
        取得指定任務的 provider。
        若 provider 初始化失敗且有 fallback，自動切換 fallback。

        Args:
            task: "sentiment" | "theme" | "pr_advisor" | "fallback"
                  （向下相容："pr" 映射到 "pr_advisor"）

        Returns:
            ModelProvider 實例
        """
        task = self._TASK_ALIASES.get(task.lower(), task.lower())
        if task in self._cache:
            return self._cache[task]

        task_config = self._get_task_config(task)

        try:
            provider = _build_provider(task_config)
            logger.debug("Router: task=%s → provider=%s", task, provider.name())
            self._cache[task] = provider
            return provider
        except Exception as e:
            logger.warning(
                "Router: task=%s provider=%s 初始化失敗：%s，嘗試 fallback...",
                task, task_config.provider, e
            )
            if task != "fallback":
                return self._get_fallback()
            raise

    def complete_with_fallback(
        self,
        task: str,
        system: str,
        user: str,
        **kwargs,
    ) -> str:
        """
        呼叫 LLM，若主 provider 失敗自動 fallback。

        Args:
            task: "sentiment" | "theme" | "pr_advisor" | "fallback"
                  （向下相容："pr" 映射到 "pr_advisor"）
            system: system prompt
            user: user prompt
            **kwargs: 傳給 provider.complete 的額外參數

        Returns:
            LLM 回應文字

        Raises:
            Exception: 主 provider 和 fallback 都失敗時
        """
        provider = self.get(task)
        try:
            return provider.complete(system=system, user=user, **kwargs)
        except Exception as primary_err:
            logger.warning(
                "Router: %s 失敗（%s），切換 fallback...",
                provider.name(), primary_err
            )
            if task == "fallback":
                raise

            fallback = self._get_fallback()
            if fallback.name() == provider.name():
                # fallback 跟主 provider 一樣，不重試
                raise primary_err

            return fallback.complete(system=system, user=user, **kwargs)

    def _get_task_config(self, task: str) -> TaskModelConfig:
        mapping = {
            "sentiment":  self._config.sentiment,
            "theme":      self._config.theme,
            "pr_advisor": self._config.pr_advisor,
            "fallback":   self._config.fallback,
        }
        return mapping.get(task, self._config.fallback)

    def _get_fallback(self) -> ModelProvider:
        return self.get("fallback")

    def __repr__(self) -> str:
        return f"ModelRouter(config={self._config})"
