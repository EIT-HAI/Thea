"""Lark-local configuration loading and Harness assembly.

The open-source channel uses the provider-neutral :class:`harness.Harness`
directly. Robot deployments can supply a composition factory through
``LARK_HARNESS_FACTORY`` without placing deployment adapters in either the
Harness or Lark packages.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from harness import BuiltinTool, Harness, ToolRegistry
from harness.configuration import (
    apply_provider_override,
    load_runtime_config_file,
)

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.example.yaml")
CONFIG_ENV = "LARK_HARNESS_CONFIG"
FACTORY_ENV = "LARK_HARNESS_FACTORY"


@dataclass(frozen=True, slots=True)
class ChannelSessionContext:
    """Stable Lark identifiers available while composing one Harness session."""

    user_id: str
    chat_id: str
    transport: str


class HarnessFactory(Protocol):
    """Public deployment boundary for composing a channel-specific Harness."""

    def __call__(
        self,
        config: dict[str, Any],
        session_context: ChannelSessionContext,
    ) -> Harness: ...


def load_runtime_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load a trusted local YAML mapping for one Lark process."""
    selected = (
        Path(path).expanduser()
        if path is not None
        else Path(os.environ.get(CONFIG_ENV, DEFAULT_CONFIG_PATH)).expanduser()
    )
    try:
        return load_runtime_config_file(selected)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Lark Harness config not found: {selected}. "
            f"Pass --config or set {CONFIG_ENV}."
        ) from exc


def create_channel_harness(
    config: Mapping[str, Any],
    *,
    session_context: ChannelSessionContext,
    factory_path: str | None = None,
) -> Harness:
    """Create a Harness for Lark, optionally through a deployment factory.

    A factory path uses ``module:callable`` syntax and receives one plain
    configuration dictionary plus the public channel session context. The
    default path has no robot-specific deployment dependencies and constructs
    :class:`Harness` directly.
    """
    normalized = dict(config)
    selected_factory = (
        str(factory_path).strip()
        if factory_path is not None
        else os.environ.get(FACTORY_ENV, "").strip()
    )
    if selected_factory:
        factory = _load_factory(selected_factory)
        harness = factory(normalized, session_context)
        if not isinstance(harness, Harness):
            raise TypeError(
                f"Harness factory {selected_factory!r} returned "
                f"{type(harness).__name__}, expected Harness."
            )
        register_channel_fallback_tools(harness.registry)
        return harness

    return Harness(
        normalized,
        builtin_registrar=register_channel_fallback_tools,
    )


def register_channel_fallback_tools(registry: ToolRegistry) -> None:
    """Ensure channel tools exist before a session installs live Lark adapters."""
    if not registry.has("query_user"):
        registry.register(
            BuiltinTool(
                name="query_user",
                description=(
                    "Ask the user a question and wait for an answer. Use this "
                    "when the instruction remains ambiguous after inspecting "
                    "the available evidence."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "description": "The concise question shown to the user.",
                        },
                        "candidate_refs": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                            },
                            "maxItems": 20,
                            "description": (
                                "Optional candidate refs from the Scene Graph Brief "
                                "to show with the question."
                            ),
                        },
                        "observation_views": _string_or_string_array_schema(
                            "Named views from the latest Observation to attach."
                        ),
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
                fn=_query_user_without_channel,
            )
        )

    if not registry.has("notify_user"):
        registry.register(
            BuiltinTool(
                name="notify_user",
                description=(
                    "Send a one-way progress, warning, or completion update to "
                    "the user. Do not use it when an answer is required."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "minLength": 1,
                            "description": "The concise update shown to the user.",
                        },
                        "notification_type": {
                            "type": "string",
                            "enum": ["progress", "warning", "completion"],
                            "default": "progress",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
                fn=_notify_user_without_channel,
            )
        )


def _load_factory(path: str) -> HarnessFactory:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        raise ValueError(
            f"Invalid {FACTORY_ENV} value {path!r}; expected module:callable."
        )
    module = importlib.import_module(module_name.strip())
    factory = getattr(module, attribute.strip(), None)
    if not callable(factory):
        raise TypeError(f"Configured Harness factory {path!r} is not callable.")
    return cast(HarnessFactory, factory)


def _string_or_string_array_schema(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "anyOf": [
            {"type": "string"},
            {
                "type": "array",
                "items": {"type": "string"},
            },
        ],
    }


def _query_user_without_channel(**_arguments: Any) -> dict[str, Any]:
    return {
        "success": False,
        "kind": "user_channel_unavailable",
        "reason": "No interactive user channel is attached to query_user.",
    }


def _notify_user_without_channel(**_arguments: Any) -> dict[str, Any]:
    return {
        "success": False,
        "kind": "user_channel_unavailable",
        "reason": "No user channel is attached to notify_user.",
    }


__all__ = [
    "ChannelSessionContext",
    "CONFIG_ENV",
    "DEFAULT_CONFIG_PATH",
    "FACTORY_ENV",
    "HarnessFactory",
    "apply_provider_override",
    "create_channel_harness",
    "load_runtime_config",
    "register_channel_fallback_tools",
]
