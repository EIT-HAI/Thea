"""Concrete LLM provider implementations."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib import error, request

from harness.context import Context, ModelResponse, ToolCall
from harness.models.anthropic_adapter import (
    anthropic_response_to_model_response,
    context_to_anthropic_messages,
    mcp_tools_to_anthropic,
)
from harness.models.errors import (
    ModelCallError,
    is_retryable_exception,
    run_with_retries,
)
from harness.models.openai_adapter import (
    context_to_openai_messages,
    mcp_tools_to_openai,
    openai_response_to_model_response,
)


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


class MockLLM:
    """Replay deterministic model decisions from a JSONL file."""

    def __init__(self, replay_path: str | Path):
        self.replay_path = Path(replay_path)
        self._decisions = self._load(self.replay_path)
        self._cursor = 0

    @staticmethod
    def _load(path: Path) -> list[ModelResponse]:
        if not path.exists():
            raise FileNotFoundError(f"replay file not found: {path}")
        decisions: list[ModelResponse] = []
        for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(("//", "#")):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: bad JSON: {exc}") from exc
            calls = [
                ToolCall(
                    id=str(item.get("id") or f"call_{index}"),
                    name=item["name"],
                    arguments=item.get("input") or item.get("arguments") or {},
                )
                for index, item in enumerate(payload.get("tool_calls") or [])
            ]
            decisions.append(
                ModelResponse(
                    text=payload.get("text", ""),
                    tool_calls=calls,
                    stop_reason=str(payload.get("stop_reason") or ""),
                )
            )
        if not decisions:
            raise ValueError(f"replay file {path} has 0 decisions")
        return decisions

    def call(self, ctx: Context, tools: list[dict] | None = None) -> ModelResponse:
        if self._cursor >= len(self._decisions):
            raise ModelCallError(
                "mock",
                1,
                RuntimeError(f"replay exhausted: {self.replay_path}"),
            )
        decision = self._decisions[self._cursor]
        self._cursor += 1
        return decision

    @property
    def remaining(self) -> int:
        return len(self._decisions) - self._cursor

    def reset(self) -> None:
        self._cursor = 0


class AnthropicLLM:
    """Anthropic Messages API provider."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ImportError(
                "AnthropicLLM requires the `anthropic` package.",
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise OSError("ANTHROPIC_API_KEY is not set.")
        # The harness owns the retry budget so the SDK must not retry again.
        self.client = Anthropic(api_key=key, timeout=timeout, max_retries=0)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries

    def call(self, ctx: Context, tools: list[dict] | None = None) -> ModelResponse:
        selected_tools = ctx.tool_definitions if tools is None else tools
        provider_tools = mcp_tools_to_anthropic(selected_tools)
        messages = context_to_anthropic_messages(ctx.messages_for_model())
        kwargs: dict[str, Any] = {
            "model": self.model,
            "system": ctx.provider_system_content,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if provider_tools:
            kwargs["tools"] = provider_tools

        def request_and_parse() -> ModelResponse:
            response = self.client.messages.create(**kwargs)
            return anthropic_response_to_model_response(response)

        return run_with_retries(
            request_and_parse,
            provider="anthropic",
            max_attempts=self.max_retries,
        )


class OpenAICompatLLM:
    """Provider using the OpenAI Python SDK and a compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        temperature: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 3,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        collapse_tool_history: bool | str | None = None,
        provider_label: str = "openai-compat",
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "OpenAICompatLLM requires the `openai` package.",
            ) from exc
        # The harness owns the retry budget so the SDK must not retry again.
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.reasoning_effort = _clean_optional(reasoning_effort)
        self.service_tier = _clean_optional(service_tier)
        self.collapse_tool_history = _truthy(collapse_tool_history)
        self.provider_label = provider_label

    def call(self, ctx: Context, tools: list[dict] | None = None) -> ModelResponse:
        selected_tools = ctx.tool_definitions if tools is None else tools
        provider_tools = mcp_tools_to_openai(selected_tools)
        messages = context_to_openai_messages(
            ctx.messages_for_model(),
            collapse_completed_tool_calls=self.collapse_tool_history,
        )
        if ctx.provider_system_content:
            messages = [
                {"role": "system", "content": ctx.provider_system_content},
                *messages,
            ]
        kwargs = _chat_completions_payload(
            model=self.model,
            messages=messages,
            tools=provider_tools,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.service_tier:
            kwargs["service_tier"] = self.service_tier

        def request_and_parse() -> ModelResponse:
            response = self.client.chat.completions.create(**kwargs)
            return openai_response_to_model_response(response)

        return run_with_retries(
            request_and_parse,
            provider=self.provider_label,
            max_attempts=self.max_retries,
        )


class _HTTPChatCompletionsLLM:
    """Shared implementation for OpenAI-compatible HTTP endpoints."""

    include_reasoning_content = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        temperature: float,
        timeout: float,
        max_retries: int,
        max_tokens: int | None,
        provider_label: str,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = _normalize_openai_base_url(base_url)
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.provider_label = provider_label

    def call(self, ctx: Context, tools: list[dict] | None = None) -> ModelResponse:
        selected_tools = ctx.tool_definitions if tools is None else tools
        provider_tools = mcp_tools_to_openai(selected_tools)
        messages = context_to_openai_messages(
            ctx.messages_for_model(),
            include_reasoning_content=self.include_reasoning_content,
        )
        if ctx.provider_system_content:
            messages = [
                {"role": "system", "content": ctx.provider_system_content},
                *messages,
            ]
        payload = _chat_completions_payload(
            model=self.model,
            messages=messages,
            tools=provider_tools,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        data = self._request_with_retries(payload)
        try:
            return openai_response_to_model_response(_to_namespace(data))
        except Exception as exc:
            raise ModelCallError(self.provider_label, 1, exc) from exc

    def _request_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        return run_with_retries(
            lambda: self._post_chat_completions(payload),
            provider=self.provider_label,
            max_attempts=self.max_retries,
        )

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _open(self, req: request.Request):
        return request.urlopen(req, timeout=self.timeout)

    def _post_chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with self._open(req) as response:
            return json.loads(response.read().decode("utf-8"))


class OpenRouterLLM(_HTTPChatCompletionsLLM):
    """OpenRouter Chat Completions provider."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openai/gpt-5.5",
        base_url: str = "https://openrouter.ai/api/v1",
        temperature: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 3,
        max_tokens: int | None = None,
        provider_label: str = "openrouter",
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=max_tokens,
            provider_label=provider_label,
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Thea"),
        }
        referer = os.environ.get("OPENROUTER_HTTP_REFERER", "").strip()
        if referer:
            headers["HTTP-Referer"] = referer
        return headers


class MiMoLLM(_HTTPChatCompletionsLLM):
    """Xiaomi MiMo token-plan Chat Completions provider."""

    include_reasoning_content = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "mimo-v2.5",
        base_url: str = "https://token-plan-cn.xiaomimimo.com/v1",
        temperature: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 3,
        max_tokens: int | None = None,
        provider_label: str = "xiaomi-mimo",
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=max_tokens,
            provider_label=provider_label,
        )
        self._http_open = request.build_opener(request.ProxyHandler({})).open

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _open(self, req: request.Request):
        return self._http_open(req, timeout=self.timeout)

    def _request_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = max(1, int(self.max_retries))
        for attempt in range(1, attempts + 1):
            try:
                return self._post_chat_completions(payload)
            except error.HTTPError as exc:
                body = _read_http_error_body(exc)
                if _mimo_image_input_unsupported(exc, body) and _has_image(payload):
                    cause = RuntimeError(
                        "MiMo endpoint rejected image input; use a "
                        "vision-capable provider/model",
                    )
                    raise ModelCallError(self.provider_label, attempt, cause) from exc
                if attempt >= attempts or not is_retryable_exception(exc):
                    raise ModelCallError(self.provider_label, attempt, exc) from exc
                time.sleep(2 ** (attempt - 1))
            except Exception as exc:
                if isinstance(exc, ModelCallError):
                    raise
                if attempt >= attempts or not is_retryable_exception(exc):
                    raise ModelCallError(self.provider_label, attempt, exc) from exc
                time.sleep(2 ** (attempt - 1))
        raise AssertionError("retry loop terminated without a result")


def _chat_completions_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float,
    max_tokens: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if max_tokens:
        payload["max_tokens"] = max_tokens
    return payload


def _to_namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(
            **{key: _to_namespace(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


def _normalize_openai_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _read_http_error_body(exc: error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except (AttributeError, OSError, UnicodeError):
        return ""


def _mimo_image_input_unsupported(exc: error.HTTPError, body: str) -> bool:
    return exc.code == 404 and "No endpoints found that support image input" in body


def _has_image(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url"}:
            return True
        return any(_has_image(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_image(item) for item in value)
    return False
