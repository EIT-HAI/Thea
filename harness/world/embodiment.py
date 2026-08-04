"""Loading for the deployment-selected Embodiment Profile."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.configuration.paths import deployment_path_candidates

EMBODIMENT_PROFILE_ITEMS: dict[str, tuple[str, ...]] = {
    "Operational Envelope": (
        "Base Footprint",
        "Base Mobility",
        "Reachable Workspace",
    ),
    "Perception Configuration": (
        "Sensor Modalities",
        "Model-Visible Views",
    ),
    "Base-Relative Positions": (
        "Camera Positions",
        "Initial Gripper Positions",
    ),
}


@dataclass(frozen=True)
class EmbodimentProfile:
    """A validated, deployment-selected Embodiment Profile document."""

    markdown: str

    @classmethod
    def from_markdown(
        cls,
        markdown: str,
        *,
        validate: bool = True,
    ) -> EmbodimentProfile:
        profile = cls(str(markdown or "").strip())
        if validate:
            profile.validate()
        return profile

    @property
    def sections(self) -> dict[str, dict[str, str]]:
        """Return the three paper-defined sections and their item bodies."""
        return _parse_profile_sections(self.markdown)

    def validation_errors(self) -> tuple[str, ...]:
        sections = self.sections
        errors: list[str] = []
        for section, required_items in EMBODIMENT_PROFILE_ITEMS.items():
            values = sections.get(section)
            if values is None:
                errors.append(f"missing section: {section}")
                continue
            for item in required_items:
                if not str(values.get(item) or "").strip():
                    errors.append(f"missing item content: {section} / {item}")
        return tuple(errors)

    def validate(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise ValueError("Invalid Embodiment Profile: " + "; ".join(errors))

    def render(self) -> str:
        """Render only the paper-defined sections and items for model context."""
        sections = self.sections
        lines = ["# Embodiment Profile"]
        for section, items in EMBODIMENT_PROFILE_ITEMS.items():
            lines.extend(["", f"## {section}"])
            values = sections.get(section, {})
            for item in items:
                lines.extend(["", f"### {item}", str(values.get(item) or "").strip()])
        return "\n".join(lines).rstrip()

    def __str__(self) -> str:
        return self.render()


def embodiment_profile_path_from_config(
    config: dict[str, Any] | None,
) -> Path | None:
    """Resolve the configured Embodiment Profile path.

    Absolute paths are used directly. Relative paths are resolved, in order,
    against ``paths.base_dir`` when configured, then the process working
    directory. Profiles are supplied by the deployment rather than bundled
    with this package.
    """
    config = config or {}
    value = config.get("embodiment_profile_file")
    if value is None:
        return None

    candidates = _path_candidates(value, config)
    return next(
        (candidate for candidate in candidates if candidate.is_file()),
        candidates[0],
    )


def load_embodiment_profile_document_from_config(
    config: dict[str, Any] | None,
    *,
    validate: bool = True,
) -> EmbodimentProfile | None:
    """Load the configured profile as a public structured document."""
    normalized_config = config or {}
    value = normalized_config.get("embodiment_profile_file")
    if value is None:
        return None
    candidates = _path_candidates(value, normalized_config)
    path = next(
        (candidate for candidate in candidates if candidate.is_file()),
        None,
    )
    if path is None:
        attempted = "\n".join(f"- {candidate}" for candidate in candidates)
        raise FileNotFoundError(
            "Configured Embodiment Profile file was not found. "
            f"Configured value: {value!s}\n"
            f"Tried, in order:\n{attempted}"
        )
    markdown = path.read_text(encoding="utf-8").strip()
    return EmbodimentProfile.from_markdown(markdown, validate=validate)


def _path_candidates(
    value: str | Path,
    config: dict[str, Any],
) -> tuple[Path, ...]:
    return deployment_path_candidates(
        value,
        config,
    )


def _parse_profile_sections(markdown: str) -> dict[str, dict[str, str]]:
    section_lookup = {
        _heading_key(section): section for section in EMBODIMENT_PROFILE_ITEMS
    }
    item_lookup = {
        section: {_heading_key(item): item for item in items}
        for section, items in EMBODIMENT_PROFILE_ITEMS.items()
    }
    sections: dict[str, dict[str, str]] = {}
    current_section = ""
    current_item = ""
    content: list[str] = []

    def flush_item() -> None:
        nonlocal content
        if current_section and current_item:
            sections.setdefault(current_section, {})[current_item] = "\n".join(
                content
            ).strip()
        content = []

    for raw_line in str(markdown or "").splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw_line.strip())
        if match:
            level = len(match.group(1))
            heading = _heading_key(match.group(2))
            next_section = section_lookup.get(heading)
            if next_section:
                flush_item()
                current_section = next_section
                current_item = ""
                sections.setdefault(current_section, {})
                continue
            if current_section:
                next_item = item_lookup[current_section].get(heading)
                if next_item:
                    flush_item()
                    current_item = next_item
                    continue
            if level == 1 and heading == _heading_key("Embodiment Profile"):
                continue
            flush_item()
            current_item = ""
            continue
        if current_section and current_item:
            content.append(raw_line)
    flush_item()
    return sections


def _heading_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


__all__ = [
    "EMBODIMENT_PROFILE_ITEMS",
    "EmbodimentProfile",
    "embodiment_profile_path_from_config",
    "load_embodiment_profile_document_from_config",
]
