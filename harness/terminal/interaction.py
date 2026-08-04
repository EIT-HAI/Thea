"""Terminal-backed implementations of the user-interaction Tools."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from harness.terminal.styling import build_terminal_console
from harness.tools.registry import BuiltinTool, ToolRegistry


class TerminalInteraction:
    """Provide ``query_user`` and ``notify_user`` through standard I/O."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output: TextIO = sys.stdout,
        console: Console | None = None,
    ) -> None:
        self._input = input_fn
        self.console = console or build_terminal_console(output)

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register terminal-backed user-interaction Tools when absent."""
        if not registry.has("query_user"):
            registry.register(
                BuiltinTool(
                    name="query_user",
                    description=(
                        "Ask the user a question and wait for an answer. Use this "
                        "when the instruction remains ambiguous after inspecting "
                        "the available evidence."
                    ),
                    input_schema=_query_user_schema(),
                    fn=self.query_user,
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
                    input_schema=_notify_user_schema(),
                    fn=self.notify_user,
                )
            )

    def query_user(
        self,
        question: str = "",
        candidate_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Ask one blocking question in the active terminal."""
        question = str(question or "").strip()
        if not question:
            return _failure("query_user_failed", "missing required argument 'question'")

        refs = list(
            dict.fromkeys(
                str(item).strip() for item in candidate_refs or [] if str(item).strip()
            )
        )
        content: list[Any] = [Text(question)]
        if refs:
            candidates = Text("\nCandidate Scene Graph refs\n", style="dim")
            for index, ref in enumerate(refs, start=1):
                candidates.append(f"  {index}. ", style="dim")
                candidates.append(ref, style="thea.accent")
                candidates.append("\n")
            content.append(candidates)
        self.console.print(
            Panel(
                Group(*content),
                title="[thea.warning]? Thea needs input[/]",
                title_align="left",
                border_style="thea.tool",
                padding=(0, 1),
            )
        )

        started = time.monotonic()
        try:
            self.console.print(
                "[thea.user]Your answer[/] [dim](/cancel to stop)[/] [thea.muted]›[/]",
                end=" ",
            )
            answer = self._input("").strip()
        except EOFError:
            answer = "/cancel"
        waited_ms = int((time.monotonic() - started) * 1000)
        if answer.lower() == "/cancel":
            return _cancelled(waited_ms, "user cancelled")
        if not answer:
            return _cancelled(waited_ms, "user returned an empty answer")
        return {
            "success": True,
            "kind": "query_user_answered",
            "waited_ms": waited_ms,
            "answer": answer,
        }

    def notify_user(
        self,
        message: str = "",
        notification_type: str = "progress",
    ) -> dict[str, Any]:
        """Write one non-blocking notification to the active terminal."""
        message = str(message or "").strip()
        notification_type = str(notification_type or "progress").strip().lower()
        if not message:
            return _failure("notify_user_failed", "missing required argument 'message'")
        presentation = {
            "progress": ("● Progress", "thea.progress", "thea.model"),
            "warning": ("▲ Warning", "thea.warning", "thea.tool"),
            "completion": ("✓ Completed", "thea.success", "thea.scene"),
        }
        if notification_type not in presentation:
            return _failure(
                "notify_user_failed",
                f"unsupported notification_type: {notification_type}",
            )
        label, title_style, border_style = presentation[notification_type]
        self.console.print(
            Panel(
                Text(message),
                title=f"[{title_style}]{label}[/]",
                title_align="left",
                border_style=border_style,
                padding=(0, 1),
            )
        )
        return {
            "success": True,
            "kind": "notify_user_sent",
            "notification_type": notification_type,
        }


def _query_user_schema() -> dict[str, Any]:
    return {
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
                    "Optional candidate refs from the Scene Graph Brief to show "
                    "with the question."
                ),
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    }


def _notify_user_schema() -> dict[str, Any]:
    return {
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
    }


def _failure(kind: str, reason: str) -> dict[str, Any]:
    return {"success": False, "kind": kind, "reason": reason}


def _cancelled(waited_ms: int, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "kind": "query_user_cancelled",
        "waited_ms": waited_ms,
        "reason": reason,
    }


__all__ = ["TerminalInteraction"]
