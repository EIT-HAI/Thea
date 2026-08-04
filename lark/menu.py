"""Menu UX for the agent-as-router surface.

Design:
  - A session has menu_state: None (LLM_MODE) or TopMenuState.
  - This module contains no action-chain logic.
  - It renders menus and parses numeric selections. The Harness Agentic Loop
    plans execution from the registered skills and tools.
"""

from __future__ import annotations

from dataclasses import dataclass

# ============================================================
# State markers
# ============================================================


@dataclass
class TopMenuState:
    """Root menu; the user is expected to select a number from 1 to 4."""


MenuState = TopMenuState


# ============================================================
# Rendering
# ============================================================


def render_top_menu() -> str:
    """Render the optional channel menu without embedding execution logic."""

    return (
        "👋 What would you like to do? Reply with a number:\n"
        "  1️⃣ Bring me an object\n"
        "  2️⃣ Describe the surroundings\n"
        "  3️⃣ Ask the AI for help (natural language)\n"
        "  4️⃣ End the conversation\n\n"
        "(Send /menu at any time to return to the main menu.)"
    )
