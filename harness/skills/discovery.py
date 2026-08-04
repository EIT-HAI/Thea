"""Canonical loading for directory-backed skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from harness.configuration.paths import deployment_path_candidates

DEFAULT_SKILLS_PATH = Path("skills")
MAX_SKILL_RESOURCE_CHARS = 50_000


@dataclass(frozen=True)
class Skill:
    """One validated directory-backed Skill and its discoverable resources."""

    name: str
    description: str
    body: str
    path: Path
    resources: tuple[str, ...] = ()


def skills_dir_from_config(config: dict[str, Any] | None) -> Path:
    """Resolve the deployment's Skill System directory.

    Absolute paths are used directly. Relative paths are resolved, in order,
    against ``paths.base_dir`` when configured, the process working directory,
    and nowhere else. Skills are supplied by the deployment rather than
    bundled with this package. The configured directory is read from
    ``skills.dir``.
    """
    config = config or {}
    value: str | Path | None = None
    skills_config = config.get("skills")
    if isinstance(skills_config, dict):
        value = skills_config.get("dir")
    requested = Path(value).expanduser() if value is not None else DEFAULT_SKILLS_PATH
    candidates = _skills_path_candidates(requested, config)
    return next(
        (candidate for candidate in candidates if candidate.is_dir()),
        candidates[0],
    )


def load_skills(skills_dir: str | Path) -> list[Skill]:
    """Load every directory-backed skill in one Skill System."""
    root = Path(skills_dir)
    if not root.exists():
        return []
    skills: list[Skill] = []
    paths_by_name: dict[str, Path] = {}
    for path in _iter_skill_files(root):
        skill = _read_skill(path)
        if skill is None:
            continue
        previous = paths_by_name.get(skill.name)
        if previous is not None:
            raise ValueError(
                f"duplicate skill name {skill.name!r}: {previous} and {path}"
            )
        paths_by_name[skill.name] = path
        skills.append(skill)
    return sorted(skills, key=lambda skill: skill.name)


def render_skill_catalog(skills: list[Skill]) -> str:
    """Render resident skill metadata without eagerly loading skill bodies."""
    entries = [
        f"- `{skill.name}`: {skill.description.strip()}"
        for skill in skills
        if skill.name.strip() and skill.description.strip()
    ]
    return "\n".join(entries)


def skill_body_by_name(skills: list[Skill], name: str) -> dict[str, Any]:
    """Return one registered skill body for progressive disclosure."""
    requested = str(name or "").strip()
    for skill in skills:
        if skill.name == requested:
            return {
                "success": True,
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.body,
                "resources": list(skill.resources),
            }
    return {
        "success": False,
        "reason": f"unknown skill: {requested}",
        "available_skills": [skill.name for skill in skills],
    }


def skill_resource_by_name(
    skills: list[Skill],
    name: str,
    resource_path: str,
) -> dict[str, Any]:
    """Read one declared bundled resource without exposing the skill directory."""
    requested_name = str(name or "").strip()
    requested_resource = str(resource_path or "").strip().replace("\\", "/")
    skill = next((item for item in skills if item.name == requested_name), None)
    if skill is None:
        return {
            "success": False,
            "reason": f"unknown skill: {requested_name}",
            "available_skills": [item.name for item in skills],
        }
    if requested_resource not in skill.resources:
        return {
            "success": False,
            "reason": f"unknown resource for skill {requested_name}: {requested_resource}",
            "available_resources": list(skill.resources),
        }

    path = skill.path.parent / requested_resource
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "success": False,
            "reason": f"skill resource is not UTF-8 text: {requested_resource}",
        }
    except OSError as exc:
        return {
            "success": False,
            "reason": f"cannot read skill resource {requested_resource}: {exc}",
        }

    truncated = len(content) > MAX_SKILL_RESOURCE_CHARS
    if truncated:
        content = content[:MAX_SKILL_RESOURCE_CHARS].rstrip() + "\n..."
    return {
        "success": True,
        "name": skill.name,
        "resource": requested_resource,
        "content": content,
        "truncated": truncated,
    }


def _iter_skill_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/SKILL.md"))


def _read_skill(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        if path.name == "SKILL.md":
            raise ValueError(f"{path}: missing YAML frontmatter")
        return None
    try:
        _, frontmatter, body = text.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"{path}: malformed YAML frontmatter") from exc
    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: YAML frontmatter must be a mapping")
    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name or not description:
        raise ValueError(f"{path}: frontmatter requires non-empty name and description")
    instructions = body.strip()
    if not instructions:
        raise ValueError(f"{path}: instruction body must not be empty")
    return Skill(
        name=name,
        description=description,
        body=instructions,
        path=path,
        resources=_discover_skill_resources(path),
    )


def _discover_skill_resources(skill_path: Path) -> tuple[str, ...]:
    """List text/script assets bundled beside a directory-backed SKILL.md."""
    if skill_path.name != "SKILL.md":
        return ()
    root = skill_path.parent
    resources: list[str] = []
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate == skill_path or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root)
        if any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ):
            continue
        resources.append(relative.as_posix())
    return tuple(sorted(resources))


def _skills_path_candidates(
    requested: Path,
    config: dict[str, Any],
) -> tuple[Path, ...]:
    return deployment_path_candidates(
        requested,
        config,
    )


__all__ = [
    "Skill",
    "load_skills",
    "render_skill_catalog",
    "skill_body_by_name",
    "skill_resource_by_name",
    "skills_dir_from_config",
]
