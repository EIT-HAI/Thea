"""Lark-free terminal entry point for the Thea Harness."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from harness.configuration import (
    apply_provider_override,
    load_runtime_config_file,
    validate_runtime_config,
)
from harness.models import ModelClient
from harness.runtime.core import Harness
from harness.terminal import (
    TerminalInteraction,
    TerminalRenderer,
    build_terminal_console,
    run_instruction,
    run_interactive,
)

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_ENV_PATH = Path(".env")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thea-cli",
        description="Run the Thea Harness with a real model in a local terminal.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("THEA_CONFIG", str(DEFAULT_CONFIG_PATH)),
        help="trusted Harness YAML configuration (default: config.yaml)",
    )
    parser.add_argument(
        "--env",
        default=str(DEFAULT_ENV_PATH),
        help="dotenv file containing the model credential (default: .env)",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="override config.yaml llm.provider",
    )
    parser.add_argument(
        "--instruction",
        help="run one instruction and exit; omit for an interactive terminal",
    )
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--failure-budget", type=int, default=20)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and model setup without making a model call",
    )
    return parser


def _load_cli_config(args: argparse.Namespace) -> tuple[dict[str, Any], Path, str]:
    env_path = Path(args.env).expanduser().resolve(strict=False)
    load_dotenv(env_path, override=False)
    config_path = Path(args.config).expanduser().resolve(strict=False)
    config = load_runtime_config_file(config_path)
    apply_provider_override(config, args.provider)
    config = validate_runtime_config(config)
    llm = config.get("llm")
    if not isinstance(llm, dict):
        raise TypeError("config.llm must be a mapping.")
    provider = str(llm.get("provider") or "anthropic").strip().lower()
    if provider == "mock":
        raise ValueError(
            "Terminal mode requires a real model provider; "
            "the mock replay provider is only a test fixture."
        )
    return config, config_path, provider


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_arguments(parser, args)

    try:
        config, config_path, provider = _load_cli_config(args)
        if args.check:
            ModelClient(dict(config.get("llm") or {}))
            print(
                f"Preflight passed: provider={provider}, mode=cli, config={config_path}"
            )
            return 0
        console = build_terminal_console()
        interaction = TerminalInteraction(console=console)
        harness = Harness(config, builtin_registrar=interaction.register_tools)
    except (ImportError, OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        renderer = TerminalRenderer(console=console)
        if args.instruction is not None:
            completed = run_instruction(
                harness,
                args.instruction.strip(),
                renderer=renderer,
                max_turns=args.max_turns,
                failure_budget=args.failure_budget,
            )
            return 0 if completed else 1
        return run_interactive(
            harness,
            renderer=renderer,
            max_turns=args.max_turns,
            failure_budget=args.failure_budget,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    finally:
        harness.close()


def _validate_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if args.max_turns < 1:
        parser.error("--max-turns must be a positive integer")
    if args.failure_budget < 1:
        parser.error("--failure-budget must be a positive integer")
    if args.instruction is not None and not args.instruction.strip():
        parser.error("--instruction must not be empty")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "TerminalInteraction",
    "TerminalRenderer",
    "build_parser",
    "main",
    "run_instruction",
    "run_interactive",
]
