"""
src/ai/config.py — 任務模型設定讀取

四種任務類型：
  sentiment   — 情感分析（快速，追求 JSON 精確度）
  theme       — 主題分類（與 sentiment 可共用同模型）
  pr_advisor  — 公關策略（長文生成，追求推理深度）
  fallback    — 備用兜底（任意任務失敗時自動切換）

環境變數格式：
  MODEL_SENTIMENT_PROVIDER   — sentiment 任務 provider（nvidia / openai / anthropic）
  MODEL_SENTIMENT_NAME       — sentiment 任務模型名稱
  MODEL_THEME_PROVIDER       — theme 任務 provider
  MODEL_THEME_NAME           — theme 任務模型名稱
  MODEL_PR_ADVISOR_PROVIDER  — pr_advisor 任務 provider
  MODEL_PR_ADVISOR_NAME      — pr_advisor 任務模型名稱
  MODEL_FALLBACK_PROVIDER    — fallback provider
  MODEL_FALLBACK_NAME        — fallback 模型名稱

未設定時全部預設 nvidia（維持現有行為）。
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskModelConfig:
    """單一任務的模型設定。"""
    provider: str        # nvidia | openai | anthropic
    model: Optional[str] = None  # None 表示用 provider 預設值

    @classmethod
    def from_env(cls, task: str, default_provider: str = "nvidia") -> "TaskModelConfig":
        """
        從環境變數讀取特定任務的模型設定。

        Args:
            task: 環境變數前綴（SENTIMENT / THEME / PR_ADVISOR / FALLBACK）
            default_provider: 預設 provider（未設定時使用）
        """
        prefix = f"MODEL_{task.upper()}"
        provider = os.getenv(f"{prefix}_PROVIDER", default_provider).lower()
        model = os.getenv(f"{prefix}_NAME", None)
        return cls(provider=provider, model=model)


@dataclass
class RouterConfig:
    """ModelRouter 的完整四任務設定。"""
    sentiment:  TaskModelConfig = field(
        default_factory=lambda: TaskModelConfig.from_env("SENTIMENT")
    )
    theme: TaskModelConfig = field(
        default_factory=lambda: TaskModelConfig.from_env("THEME")
    )
    pr_advisor: TaskModelConfig = field(
        default_factory=lambda: TaskModelConfig.from_env("PR_ADVISOR")
    )
    fallback: TaskModelConfig = field(
        default_factory=lambda: TaskModelConfig.from_env("FALLBACK")
    )

    @classmethod
    def from_env(cls) -> "RouterConfig":
        return cls(
            sentiment=TaskModelConfig.from_env("SENTIMENT"),
            theme=TaskModelConfig.from_env("THEME"),
            pr_advisor=TaskModelConfig.from_env("PR_ADVISOR"),
            fallback=TaskModelConfig.from_env("FALLBACK"),
        )

    def __repr__(self) -> str:
        return (
            f"RouterConfig("
            f"sentiment={self.sentiment.provider}/{self.sentiment.model}, "
            f"theme={self.theme.provider}/{self.theme.model}, "
            f"pr_advisor={self.pr_advisor.provider}/{self.pr_advisor.model}, "
            f"fallback={self.fallback.provider}/{self.fallback.model})"
        )
