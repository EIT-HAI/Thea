"""Trusted runtime-configuration loading shared by public entry points."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


def load_runtime_config_file(path: str | Path) -> dict[str, Any]:
    """Load one trusted local YAML mapping and apply runtime defaults."""
    selected = Path(path).expanduser()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Harness config not found: {selected}.") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in Harness config {selected}: {exc}") from exc

    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise TypeError(
            f"Harness config must be a YAML mapping, got {type(payload).__name__}."
        )

    config = dict(payload)
    config.setdefault("servers", [])
    config.setdefault("llm", {})
    return config


def apply_provider_override(
    config: dict[str, Any],
    provider: str | None,
) -> dict[str, Any]:
    """Apply a provider selection without retaining incompatible fields."""
    selected = str(provider or "").strip().lower()
    if not selected:
        return config

    llm = config.setdefault("llm", {})
    if not isinstance(llm, dict):
        raise TypeError("config.llm must be a mapping before provider override.")

    previous = str(llm.get("provider") or "").strip().lower()
    if previous != selected:
        for key in (
            "api_key",
            "api_key_env",
            "base_url",
            "collapse_tool_history",
            "model",
            "reasoning_effort",
            "replay_path",
            "service_tier",
        ):
            llm.pop(key, None)
    llm["provider"] = selected
    return config


__all__ = ["apply_provider_override", "load_runtime_config_file"]
