"""Render the paper's Resident and Refreshed context lifetimes."""

from __future__ import annotations

from collections.abc import Sequence

from harness.context.system_prompt import SYSTEM_PROMPT, render_system_prompt

ContextBlock = tuple[str, str]


def _nest_markdown_headings(text: str, *, min_level: int) -> str:
    """Keep nested headings from becoming top-level context sections."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        heading_level = _markdown_heading_level(stripped)
        if heading_level is not None:
            indent = line[: len(line) - len(stripped)]
            nested_level = max(min_level, heading_level + 1)
            line = f"{indent}{'#' * nested_level}{stripped[heading_level:]}"
        lines.append(line)
    return "\n".join(lines).strip()


def _markdown_heading_level(text: str) -> int | None:
    marker = text.split(" ", 1)[0]
    if (
        marker
        and set(marker) == {"#"}
        and len(text) > len(marker)
        and text[len(marker)] == " "
    ):
        return len(marker)
    return None


def _render_context_block(title: str, content: str) -> str:
    title = str(title or "").strip().lstrip("#").strip()
    content = _nest_markdown_headings(str(content or "").strip(), min_level=3)
    if not title or not content:
        return ""
    return f"## {title}\n{content}"


def _render_embodiment_profile(content: str) -> str:
    content = str(content or "").strip()
    if not content:
        return ""

    lines = content.splitlines()
    body_lines = lines
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped:
            continue
        if (
            _markdown_heading_level(stripped) == 1
            and stripped[1:].strip().casefold() == "embodiment profile"
        ):
            body_lines = lines[:index] + lines[index + 1 :]
        break

    body = _nest_markdown_headings("\n".join(body_lines).strip(), min_level=3)
    if not body:
        return "## Embodiment Profile"
    return f"## Embodiment Profile\n{body}"


def build_resident_context(
    *,
    system_prompt: str = SYSTEM_PROMPT,
    memory: str = "",
    embodiment_profile: str = "",
    skill_catalog: str = "",
    task_context_blocks: Sequence[ContextBlock] | None = None,
) -> str:
    """Render context that remains Resident throughout one task."""
    sections = [render_system_prompt(system_prompt)]
    if memory:
        sections.append(_render_context_block("Memory", memory))
    if embodiment_profile:
        sections.append(_render_embodiment_profile(embodiment_profile))
    if skill_catalog:
        sections.append(_render_context_block("Skill Catalog", skill_catalog))
    for block in task_context_blocks or []:
        try:
            title, content = block
        except (TypeError, ValueError):
            continue
        rendered = _render_context_block(title, content)
        if rendered:
            sections.append(rendered)

    return "\n\n".join(section for section in sections if section)


def build_refreshed_context(
    *,
    scene_graph_brief: str = "",
) -> str:
    """Render context that is replaced before each model decision."""
    if not scene_graph_brief:
        return ""
    return _render_context_block("Scene Graph Brief", scene_graph_brief)


def build_provider_system_content(
    loaded_skill_context_blocks: Sequence[ContextBlock] | None = None,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    memory: str = "",
    embodiment_profile: str = "",
    skill_catalog: str = "",
) -> str:
    """Render Resident context for a provider's system field."""
    return build_resident_context(
        system_prompt=system_prompt,
        memory=memory,
        embodiment_profile=embodiment_profile,
        skill_catalog=skill_catalog,
        task_context_blocks=loaded_skill_context_blocks,
    )


__all__ = [
    "ContextBlock",
    "build_provider_system_content",
    "build_refreshed_context",
    "build_resident_context",
]
