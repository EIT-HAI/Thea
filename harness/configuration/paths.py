"""Path resolution shared by deployment-selected harness resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def deployment_path_candidates(
    value: str | Path,
    config: dict[str, Any],
) -> tuple[Path, ...]:
    """Resolve one deployment-owned path independently of package layout.

    Relative paths are considered against ``paths.base_dir`` when configured,
    then the process working directory. Deployment resources are not looked up
    in a source checkout or installed package tree.
    """
    requested = Path(value).expanduser()
    if requested.is_absolute():
        return (requested.resolve(strict=False),)

    candidates: list[Path] = []
    base_dir = configured_base_dir(config)
    if base_dir is not None:
        candidates.append(base_dir / requested)
    candidates.append(Path.cwd() / requested)
    return unique_absolute_paths(candidates)


def configured_base_dir(config: dict[str, Any]) -> Path | None:
    """Return ``paths.base_dir`` resolved from the process working directory."""
    paths_config = config.get("paths")
    if not isinstance(paths_config, dict):
        return None
    value = paths_config.get("base_dir")
    if value is None:
        return None
    base_dir = Path(value).expanduser()
    if not base_dir.is_absolute():
        base_dir = Path.cwd() / base_dir
    return base_dir.resolve(strict=False)


def unique_absolute_paths(paths: list[Path]) -> tuple[Path, ...]:
    """Return absolute candidates in order with duplicates removed."""
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return tuple(unique)


__all__ = [
    "configured_base_dir",
    "deployment_path_candidates",
    "unique_absolute_paths",
]
