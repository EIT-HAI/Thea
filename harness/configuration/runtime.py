"""Validation for the small trusted runtime configuration surface."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


class ConfigurationError(ValueError):
    """Raised before startup when runtime configuration is malformed."""


def validate_runtime_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow copy after validating harness-owned config sections."""
    if not isinstance(config, Mapping):
        raise ConfigurationError("Harness config must be a mapping.")
    normalized = dict(config)

    _optional_mapping(normalized, "llm")
    _optional_mapping(normalized, "context")
    _optional_mapping(normalized, "memory")
    _optional_mapping(normalized, "scene_graph")
    _optional_mapping(normalized, "safety")
    _optional_mapping(normalized, "skills")
    _optional_mapping(normalized, "paths")
    _optional_mapping(normalized, "evaluation")
    _validate_optional_path(
        normalized.get("embodiment_profile_file"),
        path="config.embodiment_profile_file",
    )
    _validate_context(normalized.get("context"))
    _validate_evaluation(normalized.get("evaluation"))
    _validate_safety(normalized.get("safety"))
    _validate_skills(normalized.get("skills"))
    _validate_paths(normalized.get("paths"))

    servers = normalized.get("servers", [])
    if servers is None:
        servers = []
    if not isinstance(servers, list):
        raise ConfigurationError("config.servers must be a list.")
    server_names: set[str] = set()
    for index, server in enumerate(servers):
        _validate_server(server, index=index)
        server_name = str(server.get("name") or "unnamed").strip()
        if server_name in server_names:
            raise ConfigurationError(
                f"config.servers contains duplicate server name {server_name!r}."
            )
        server_names.add(server_name)
    normalized["servers"] = list(servers)
    return normalized


def _validate_context(value: Any) -> None:
    if value is None:
        return
    compaction = value.get("compaction")
    if compaction is None:
        return
    if not isinstance(compaction, Mapping):
        raise ConfigurationError("config.context.compaction must be a mapping.")
    enabled = compaction.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ConfigurationError("config.context.compaction.enabled must be a boolean.")
    for key in (
        "context_window",
        "reserve_tokens",
        "keep_recent_turns",
        "keep_recent_tokens",
        "tool_result_max_chars",
        "reasoning_max_chars",
        "max_input_chars",
        "max_summary_rounds",
    ):
        item = compaction.get(key)
        if item is None:
            continue
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ConfigurationError(
                f"config.context.compaction.{key} must be a positive integer."
            )
    context_window = compaction.get("context_window", 200_000)
    reserve_tokens = compaction.get("reserve_tokens", 16_384)
    if (
        isinstance(context_window, int)
        and not isinstance(context_window, bool)
        and isinstance(reserve_tokens, int)
        and not isinstance(reserve_tokens, bool)
        and reserve_tokens >= context_window
    ):
        raise ConfigurationError(
            "config.context.compaction.reserve_tokens must be smaller than "
            "context_window."
        )


def _validate_evaluation(value: Any) -> None:
    if value is None:
        return
    _validate_string_list(
        value.get("required_tools", []),
        path="config.evaluation.required_tools",
    )
    _validate_string_list(
        value.get("segment_tools", []),
        path="config.evaluation.segment_tools",
    )
    post_conditions = value.get("post_conditions", {})
    if not isinstance(post_conditions, Mapping) or not all(
        isinstance(name, str)
        and name.strip()
        and isinstance(post_condition, str)
        and post_condition.strip()
        for name, post_condition in post_conditions.items()
    ):
        raise ConfigurationError(
            "config.evaluation.post_conditions must map non-empty tool names "
            "to non-empty post-condition strings."
        )
    max_segments = value.get("max_segments", 8)
    if (
        isinstance(max_segments, bool)
        or not isinstance(max_segments, int)
        or max_segments < 1
    ):
        raise ConfigurationError(
            "config.evaluation.max_segments must be a positive integer."
        )


def _validate_safety(value: Any) -> None:
    if value is None:
        return
    margin = value.get("base_clearance_margin_m")
    if margin is None:
        return
    if (
        isinstance(margin, bool)
        or not isinstance(margin, (int, float))
        or not math.isfinite(float(margin))
        or float(margin) < 0
    ):
        raise ConfigurationError(
            "config.safety.base_clearance_margin_m must be a finite, "
            "non-negative number."
        )


def _validate_skills(value: Any) -> None:
    if value is None:
        return
    _validate_optional_path(value.get("dir"), path="config.skills.dir")


def _validate_paths(value: Any) -> None:
    if value is None:
        return
    _validate_optional_path(value.get("base_dir"), path="config.paths.base_dir")


def _validate_optional_path(value: Any, *, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{path} must be a non-empty string path.")


def _validate_string_list(value: Any, *, path: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigurationError(f"{path} must be a list of non-empty strings.")


def _optional_mapping(config: dict[str, Any], key: str) -> None:
    value = config.get(key)
    if value is not None and not isinstance(value, Mapping):
        raise ConfigurationError(f"config.{key} must be a mapping.")


def _validate_server(value: Any, *, index: int) -> None:
    prefix = f"config.servers[{index}]"
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{prefix} must be a mapping.")
    command = value.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ConfigurationError(f"{prefix}.command must be a non-empty string.")
    args = value.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ConfigurationError(f"{prefix}.args must be a list of strings.")
    env = value.get("env")
    if env is not None and (
        not isinstance(env, Mapping)
        or not all(
            isinstance(key, str) and isinstance(item, (str, int, float, bool))
            for key, item in env.items()
        )
    ):
        raise ConfigurationError(
            f"{prefix}.env must map string names to scalar values."
        )
    cwd = value.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
        raise ConfigurationError(f"{prefix}.cwd must be a non-empty string path.")
    name = value.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ConfigurationError(f"{prefix}.name must be a non-empty string.")
    _validate_finite_number(
        value.get("call_timeout_sec"),
        path=f"{prefix}.call_timeout_sec",
        minimum=0,
        minimum_inclusive=False,
    )
    _validate_finite_number(
        value.get("timeout_argument_margin_sec"),
        path=f"{prefix}.timeout_argument_margin_sec",
        minimum=0,
        minimum_inclusive=True,
    )
    transport = value.get("transport", "stdio")
    if transport != "stdio":
        raise ConfigurationError(
            f"{prefix}.transport={transport!r} is unsupported; expected 'stdio'."
        )


def _validate_finite_number(
    value: Any,
    *,
    path: str,
    minimum: float,
    minimum_inclusive: bool,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{path} must be a finite number.")
    number = float(value)
    valid_minimum = number >= minimum if minimum_inclusive else number > minimum
    if not math.isfinite(number) or not valid_minimum:
        qualifier = "non-negative" if minimum_inclusive else "positive"
        raise ConfigurationError(f"{path} must be a finite, {qualifier} number.")


__all__ = ["ConfigurationError", "validate_runtime_config"]
