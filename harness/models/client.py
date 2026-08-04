"""LLM provider selection and the runtime client facade."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from harness.context import Context, ModelResponse
from harness.models.errors import ModelCallError
from harness.models.providers import (
    AnthropicLLM,
    MiMoLLM,
    MockLLM,
    OpenAICompatLLM,
    OpenRouterLLM,
)


class _ModelImpl(Protocol):
    def call(self, ctx: Context, tools: list[dict] | None = None) -> ModelResponse: ...


def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise OSError(f"Environment variable {key} is not set.")
    return value


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_env(*keys: str) -> str:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _require_any_env(*keys: str) -> str:
    value = _first_env(*keys)
    if value:
        return value
    raise OSError(
        f"Environment variable {keys[0]} is not set; accepted keys: " + ", ".join(keys),
    )


def _effective_provider(provider: str | None) -> str:
    return (provider or os.environ.get("LLM_PROVIDER", "anthropic")).strip().lower()


def _resolve_config_api_key(config: dict[str, Any]) -> str | None:
    direct_key = _clean_optional(config.get("api_key"))
    if direct_key:
        return direct_key
    env_key = _clean_optional(config.get("api_key_env"))
    if not env_key:
        return None
    value = os.environ.get(env_key, "").strip()
    if value:
        return value
    raise OSError(
        f"api_key_env points to unset environment variable {env_key!r}.",
    )


def build_model(
    provider: str | None = None,
    *,
    api_key: str | None = None,
    model: str | None = None,
    replay_path: str | Path | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
    collapse_tool_history: bool | str | None = None,
) -> _ModelImpl:
    """Build the configured provider implementation."""
    provider = _effective_provider(provider)
    if provider == "mock":
        path = _clean_optional(replay_path) or _clean_optional(
            os.environ.get("LLM_REPLAY")
        )
        if path is None:
            raise ValueError(
                "The mock provider requires an explicit replay_path or "
                "the LLM_REPLAY environment variable."
            )
        return MockLLM(Path(path).expanduser())
    if provider == "anthropic":
        return AnthropicLLM(
            api_key=_clean_optional(api_key) or _require_env("ANTHROPIC_API_KEY"),
            model=model
            or os.environ.get(
                "ANTHROPIC_MODEL",
                "claude-sonnet-4-20250514",
            ),
            max_tokens=max_tokens if max_tokens is not None else 4096,
        )
    if provider == "openai":
        return OpenAICompatLLM(
            api_key=_clean_optional(api_key) or _require_env("OPENAI_API_KEY"),
            model=model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            base_url=base_url or os.environ.get("OPENAI_BASE_URL") or None,
            max_tokens=max_tokens,
            reasoning_effort=(
                _clean_optional(reasoning_effort)
                or _first_env("OPENAI_REASONING_EFFORT", "LLM_REASONING_EFFORT")
            ),
            service_tier=(
                _clean_optional(service_tier)
                or _first_env("OPENAI_SERVICE_TIER", "LLM_SERVICE_TIER")
            ),
            collapse_tool_history=collapse_tool_history,
            provider_label="openai",
        )
    if provider == "openrouter":
        return OpenRouterLLM(
            api_key=_clean_optional(api_key) or _require_env("OPENROUTER_API_KEY"),
            model=model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.5"),
            base_url=base_url
            or os.environ.get(
                "OPENROUTER_BASE_URL",
                "https://openrouter.ai/api/v1",
            ),
            max_tokens=max_tokens,
        )
    if provider in {"mimo", "xiaomi_mimo", "xiaomi"}:
        return MiMoLLM(
            api_key=(
                _clean_optional(api_key)
                or _require_any_env("MIMO_API_KEY", "XIAOMI_API_KEY", "MI_API_KEY")
            ),
            model=(
                model or _first_env("MIMO_MODEL", "XIAOMI_MIMO_MODEL") or "mimo-v2.5"
            ),
            base_url=(
                base_url
                or _first_env("MIMO_BASE_URL", "XIAOMI_MIMO_BASE_URL")
                or "https://token-plan-cn.xiaomimimo.com/v1"
            ),
            max_tokens=max_tokens,
        )
    if provider == "qwen":
        return OpenAICompatLLM(
            api_key=_clean_optional(api_key) or _require_env("DASHSCOPE_API_KEY"),
            model=model or os.environ.get("QWEN_MODEL", "qwen-max-latest"),
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            max_tokens=max_tokens,
            provider_label="qwen-dashscope",
        )
    if provider == "deepseek":
        return OpenAICompatLLM(
            api_key=_clean_optional(api_key) or _require_env("DEEPSEEK_API_KEY"),
            model=model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=base_url or "https://api.deepseek.com/v1",
            max_tokens=max_tokens,
            provider_label="deepseek",
        )
    if provider == "ollama":
        return OpenAICompatLLM(
            api_key="ollama",
            model=model or os.environ.get("OLLAMA_MODEL", "llama3.2"),
            base_url=base_url
            or os.environ.get(
                "OLLAMA_BASE_URL",
                "http://localhost:11434/v1",
            ),
            max_tokens=max_tokens,
            provider_label="ollama",
        )
    raise ValueError(
        f"unknown LLM_PROVIDER: {provider!r}. Supported: "
        "mock, anthropic, openai, openrouter, mimo, qwen, deepseek, ollama",
    )


class ModelClient:
    """Facade that resolves one provider from the runtime configuration."""

    def __init__(self, config: dict[str, Any]):
        self.config = dict(config)
        provider = self.config.get("provider")
        config_api_key = None
        if _effective_provider(provider) not in {"mock", "ollama"}:
            config_api_key = _resolve_config_api_key(self.config)
        self._impl = build_model(
            provider,
            api_key=config_api_key,
            model=self.config.get("model"),
            replay_path=self.config.get("replay_path"),
            base_url=self.config.get("base_url"),
            max_tokens=self.config.get("max_tokens"),
            reasoning_effort=self.config.get("reasoning_effort"),
            service_tier=self.config.get("service_tier"),
            collapse_tool_history=self.config.get("collapse_tool_history"),
        )

    def call(self, ctx: Context, tools: list[dict] | None = None) -> ModelResponse:
        return self._impl.call(ctx, tools)


__all__ = [
    "AnthropicLLM",
    "ModelCallError",
    "ModelClient",
    "MiMoLLM",
    "MockLLM",
    "OpenAICompatLLM",
    "OpenRouterLLM",
    "build_model",
]
