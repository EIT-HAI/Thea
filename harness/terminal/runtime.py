"""Task and session lifecycle for the terminal channel."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from harness.observability import run_logger
from harness.runtime.core import Harness
from harness.terminal.presentation import TerminalRenderer


def run_instruction(
    harness: Harness,
    instruction: str,
    *,
    renderer: TerminalRenderer,
    max_turns: int,
    failure_budget: int,
) -> bool:
    """Run one Task, persist its events, and return completion status."""
    completed = False
    with run_logger() as (log_path, log):
        for event in harness.run_stream(
            instruction,
            max_turns=max_turns,
            failure_budget=failure_budget,
        ):
            log(event)
            renderer.render(event)
            if event.get("type") == "done":
                completed = event.get("task_status") == "completed"
    renderer.render_log_path(log_path)
    return completed


def run_interactive(
    harness: Harness,
    *,
    input_fn: Callable[[str], str] = input,
    renderer: TerminalRenderer | None = None,
    output: TextIO = sys.stdout,
    max_turns: int = 100,
    failure_budget: int = 20,
) -> int:
    """Read instructions until EOF or an explicit terminal command."""
    renderer = renderer or TerminalRenderer(output)
    renderer.render_banner()
    while True:
        try:
            instruction = renderer.read_instruction(input_fn).strip()
        except EOFError:
            renderer.console.print()
            return 0
        if not instruction:
            continue
        if instruction.lower() in {"/quit", "/exit"}:
            return 0
        if instruction.lower() == "/reset":
            harness.reset_session()
            renderer.render_session_reset()
            continue
        run_instruction(
            harness,
            instruction,
            renderer=renderer,
            max_turns=max_turns,
            failure_budget=failure_budget,
        )


__all__ = ["run_instruction", "run_interactive"]
