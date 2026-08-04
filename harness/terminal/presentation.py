"""Rich terminal rendering for Harness run events."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from harness.terminal.styling import TheaSyntaxStyle, build_terminal_console


class TerminalRenderer:
    """Render stable Harness events through one accessible Rich console."""

    def __init__(
        self,
        output: TextIO = sys.stdout,
        *,
        console: Console | None = None,
    ) -> None:
        self.console = console or build_terminal_console(output)

    def render_banner(self) -> None:
        title = Text("THEA", style="thea.accent", justify="center")
        subtitle = Text(
            "Embodied Agent Harness · Terminal",
            style="thea.muted",
            justify="center",
        )
        self.console.print(
            Panel.fit(
                Group(title, subtitle),
                border_style="thea.scene",
                padding=(0, 3),
            )
        )
        self.console.print(
            "[dim]Enter an instruction · /reset clears context · /quit exits[/]"
        )

    def read_instruction(self, input_fn: Callable[[str], str]) -> str:
        self.console.print("\n[thea.user]You[/] [thea.muted]›[/]", end=" ")
        return input_fn("")

    def render_session_reset(self) -> None:
        self.console.print("[thea.success]✓[/] Session context cleared.")

    def render_log_path(self, path: Path) -> None:
        self.console.print(f"[dim]Run log  {path}[/]")

    def render(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        turn = event.get("turn", "-")
        if event_type == "model_response":
            self._render_reasoning(event, turn)
        elif event_type == "say":
            self._render_say(event, turn)
        elif event_type == "tool_call":
            self._render_tool_call(event, turn)
        elif event_type == "tool_result":
            self._render_tool_result(event, turn)
        elif event_type in {"model_error", "harness_error", "error"}:
            self._render_error(event)
        elif event_type == "done":
            self._render_final(event)

    def _render_reasoning(self, event: dict[str, Any], turn: Any) -> None:
        reasoning = str(event.get("reasoning_content") or "").strip()
        if not reasoning:
            return
        self.console.print(
            Panel(
                Markdown(reasoning),
                title=f"[thea.reasoning]Turn {turn} · Reasoning[/]",
                title_align="left",
                border_style="thea.model",
                padding=(0, 1),
            )
        )

    def _render_say(self, event: dict[str, Any], turn: Any) -> None:
        text = str(event.get("text") or "").strip()
        if not text:
            return
        self.console.print(
            Panel(
                Markdown(text),
                title=f"[thea.say]Turn {turn} · Thea[/]",
                title_align="left",
                border_style="thea.model",
                padding=(0, 1),
            )
        )

    def _render_tool_call(self, event: dict[str, Any], turn: Any) -> None:
        arguments = json.dumps(
            event.get("arguments") or {},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        name = str(event.get("name") or "unknown_tool")
        self.console.print(
            Panel(
                Syntax(
                    arguments,
                    "json",
                    theme=TheaSyntaxStyle,
                    background_color="default",
                    word_wrap=True,
                ),
                title=f"[thea.tool]Turn {turn} · Tool · {name}[/]",
                title_align="left",
                border_style="thea.tool",
                padding=(0, 1),
            )
        )

    def _render_tool_result(self, event: dict[str, Any], turn: Any) -> None:
        result = event.get("result")
        result = result if isinstance(result, dict) else {}
        succeeded = bool(event.get("success"))
        name = str(event.get("name") or "unknown_tool")
        detail = str(result.get("reason") or result.get("observation") or "")
        line = Text()
        line.append(
            "✓ " if succeeded else "✗ ",
            style=("thea.success" if succeeded else "thea.failure"),
        )
        line.append(f"Turn {turn} · {name} · ")
        line.append(
            "success" if succeeded else "failed",
            style=("thea.success" if succeeded else "thea.failure"),
        )
        if detail:
            line.append(f"  {detail}", style="dim")
        self.console.print(line)

    def _render_error(self, event: dict[str, Any]) -> None:
        detail = str(event.get("error") or event.get("reason") or "unknown error")
        self.console.print(
            Panel(
                Text(detail),
                title="[thea.failure]Error[/]",
                title_align="left",
                border_style="thea.evaluator",
            )
        )

    def _render_final(self, event: dict[str, Any]) -> None:
        final_text = str(event.get("final_text") or "").strip() or "[no final text]"
        completed = event.get("task_status") == "completed"
        style = "thea.scene" if completed else "thea.tool"
        title_style = "thea.final" if completed else "thea.warning"
        symbol = "✓" if completed else "!"
        self.console.print(
            Panel(
                Markdown(final_text),
                title=f"[{title_style}]{symbol} Thea · Final[/]",
                title_align="left",
                border_style=style,
                padding=(1, 2),
            )
        )


__all__ = ["TerminalRenderer"]
