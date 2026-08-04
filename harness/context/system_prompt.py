"""The resident System Prompt and its override boundary."""

from __future__ import annotations

SYSTEM_PROMPT = """## System Prompt
You are Thea, an embodied agent that completes physical tasks by calling tools.

- Choose one next tool call per turn unless the task is complete.
- After each tool result, update the plan from the latest evidence.
- Use the Scene Graph Brief for object refs and coarse global state. Use the
  latest Observation for current local visibility, alignment, and clearance.
- Treat the latest Observation as current physical evidence and earlier tool
  results as historical execution evidence.
- Follow tool descriptions for preconditions, argument semantics, failure modes,
  and recovery hints.
- Do not invent object identifiers, measurements, action outcomes, or user
  intent. Ask the user when the instruction and current evidence are genuinely
  insufficient.
- Wait for the current tool to finish before choosing the next tool."""


def render_system_prompt(content: str) -> str:
    """Render exactly one System Prompt block inside Resident context."""
    text = str(content or "").strip()
    if not text:
        text = SYSTEM_PROMPT
    if text.startswith("## System Prompt"):
        return text
    return f"## System Prompt\n{text}"


def extract_system_prompt(content: str) -> str:
    """Extract only the System Prompt from captured model context.

    Historical reruns may supply a snapshot containing every Resident and
    Refreshed block. Only the System Prompt is replaceable. Memory, Embodiment
    Profile, Skill Catalog, loaded skill content, and the latest Scene Graph
    Brief remain harness-owned.
    """
    text = str(content or "").strip()
    if not text:
        return SYSTEM_PROMPT
    lines = text.splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == "## System Prompt"
        ),
        None,
    )
    if start is None:
        return render_system_prompt(text)

    retained = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        retained.append(line)
    return "\n".join(retained).strip()


__all__ = [
    "SYSTEM_PROMPT",
    "extract_system_prompt",
    "render_system_prompt",
]
