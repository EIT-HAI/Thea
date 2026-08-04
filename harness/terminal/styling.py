"""Shared visual language for the terminal channel."""

from __future__ import annotations

import sys
from typing import TextIO

from pygments.style import Style
from pygments.token import Comment, Keyword, Name, Number, Operator, Punctuation, String
from rich.console import Console
from rich.theme import Theme

# Colors sampled from the paper's block diagram: Model, Scene Graph, Tool,
# Body, and Evaluator. The terminal uses the darker outline colors so that the
# same palette remains legible on both light and dark terminal backgrounds.
THEA_MODEL = "#5F7288"
THEA_SCENE = "#6C8583"
THEA_TOOL = "#B09E76"
THEA_BODY = "#83705C"
THEA_EVALUATOR = "#8D7B80"
THEA_NEUTRAL = "#A3A19E"

THEA_THEME = Theme(
    {
        "thea.accent": f"bold {THEA_SCENE}",
        "thea.muted": f"dim {THEA_NEUTRAL}",
        "thea.user": f"bold {THEA_MODEL}",
        "thea.reasoning": f"bold {THEA_MODEL}",
        "thea.say": f"bold {THEA_MODEL}",
        "thea.tool": f"bold {THEA_TOOL}",
        "thea.success": f"bold {THEA_SCENE}",
        "thea.failure": f"bold {THEA_EVALUATOR}",
        "thea.warning": f"bold {THEA_TOOL}",
        "thea.progress": f"bold {THEA_MODEL}",
        "thea.final": f"bold {THEA_SCENE}",
        "thea.model": THEA_MODEL,
        "thea.scene": THEA_SCENE,
        "thea.body": THEA_BODY,
        "thea.evaluator": THEA_EVALUATOR,
        "thea.neutral": THEA_NEUTRAL,
    }
)


class TheaSyntaxStyle(Style):
    """Pygments style derived from the paper palette for Tool arguments."""

    background_color = None
    default_style = ""
    styles = {
        Comment: f"italic {THEA_NEUTRAL}",
        Keyword: THEA_EVALUATOR,
        Keyword.Constant: THEA_EVALUATOR,
        Name: THEA_SCENE,
        Name.Tag: THEA_SCENE,
        Number: THEA_TOOL,
        Operator: THEA_NEUTRAL,
        Punctuation: THEA_NEUTRAL,
        String: THEA_SCENE,
    }


def build_terminal_console(output: TextIO = sys.stdout) -> Console:
    """Create one Rich console that respects TTY and ``NO_COLOR`` settings."""
    return Console(
        file=output,
        theme=THEA_THEME,
        highlight=False,
        soft_wrap=False,
    )


__all__ = ["THEA_THEME", "TheaSyntaxStyle", "build_terminal_console"]
