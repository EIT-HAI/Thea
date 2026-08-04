from __future__ import annotations

from pathlib import Path

import pytest
from harness.configuration.paths import deployment_path_candidates
from harness.configuration.runtime import ConfigurationError, validate_runtime_config
from harness.models import MockLLM, build_model
from harness.observability import default_log_dir, run_logger
from harness.skills.discovery import load_skills, skills_dir_from_config
from harness.skills.runtime import SkillSystem
from harness.tools.registry import ToolRegistry
from harness.world.embodiment import (
    EmbodimentProfile,
    load_embodiment_profile_document_from_config,
)


def test_mock_provider_requires_explicit_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_REPLAY", raising=False)

    with pytest.raises(ValueError, match="explicit replay_path"):
        build_model("mock")


def test_mock_provider_accepts_configured_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = tmp_path / "decisions.jsonl"
    replay.write_text('{"text": "done"}\n', encoding="utf-8")
    monkeypatch.delenv("LLM_REPLAY", raising=False)

    model = build_model("mock", replay_path=replay)

    assert isinstance(model, MockLLM)
    assert model.replay_path == replay
    assert model.remaining == 1


def test_mock_provider_accepts_replay_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = tmp_path / "environment-decisions.jsonl"
    replay.write_text('{"text": "done"}\n', encoding="utf-8")
    monkeypatch.setenv("LLM_REPLAY", str(replay))

    model = build_model("mock")

    assert isinstance(model, MockLLM)
    assert model.replay_path == replay


def test_deployment_paths_do_not_depend_on_source_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_dir = tmp_path / "working"
    deployment_dir = tmp_path / "deployment"
    working_dir.mkdir()
    (deployment_dir / "skills").mkdir(parents=True)
    monkeypatch.chdir(working_dir)

    candidates = deployment_path_candidates(
        "skills",
        {"paths": {"base_dir": deployment_dir}},
    )

    assert candidates == (
        (deployment_dir / "skills").resolve(),
        (working_dir / "skills").resolve(),
    )
    assert (
        skills_dir_from_config(
            {
                "paths": {"base_dir": deployment_dir},
                "skills": {"dir": "skills"},
            }
        )
        == (deployment_dir / "skills").resolve()
    )


def test_external_skills_and_profile_resolve_from_deployment_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_dir = tmp_path / "working"
    deployment_dir = tmp_path / "deployment"
    skill_dir = deployment_dir / "skills" / "inspect"
    profile = deployment_dir / "profiles" / "robot.md"
    working_dir.mkdir()
    skill_dir.mkdir(parents=True)
    profile.parent.mkdir()
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: inspect\ndescription: Inspect the deployment.\n---\n"
        "Follow deployment policy.\n",
        encoding="utf-8",
    )
    profile.write_text("# Embodiment Profile\nmobile base\n", encoding="utf-8")
    monkeypatch.chdir(working_dir)
    config = {
        "paths": {"base_dir": deployment_dir},
        "skills": {"dir": "skills"},
        "embodiment_profile_file": "profiles/robot.md",
    }

    skills = load_skills(skills_dir_from_config(config))

    assert [skill.name for skill in skills] == ["inspect"]
    document = load_embodiment_profile_document_from_config(config, validate=False)
    assert document is not None
    assert document.markdown == "# Embodiment Profile\nmobile base"


def test_skill_requires_an_instruction_body(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "empty"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: empty\ndescription: Empty skill.\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="instruction body must not be empty"):
        load_skills(tmp_path / "skills")


def test_skill_resource_tool_uses_load_terminology(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "inspect"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\n"
        "name: inspect\n"
        "description: Inspect an object.\n"
        "---\n"
        "Follow the inspection procedure.\n",
        encoding="utf-8",
    )
    skill_dir.joinpath("checklist.md").write_text(
        "Inspection checklist.\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()

    SkillSystem(load_skills(tmp_path / "skills")).register_tools(registry)

    names = [item["name"] for item in registry.list_tool_definitions()]
    assert names == ["load_skill", "load_skill_resource"]


def test_embodiment_profile_public_schema() -> None:
    profile = EmbodimentProfile.from_markdown(
        """# Embodiment Profile

## Operational Envelope
### Base Footprint
0.4 m radius.
### Base Mobility
Planar translation and yaw.
### Reachable Workspace
0.2-1.2 m above the floor.

## Perception Configuration
### Sensor Modalities
Color images and base-distance measurements.
### Model-Visible Views
Head and chest.

## Base-Relative Positions
### Camera Positions
Head and chest camera positions relative to the base center.
### Initial Gripper Positions
Left and right gripper positions in the initial posture.
"""
    )

    assert profile.validation_errors() == ()
    assert profile.sections["Operational Envelope"]["Base Mobility"] == (
        "Planar translation and yaw."
    )


def test_embodiment_profile_context_projects_only_paper_defined_items() -> None:
    profile = EmbodimentProfile.from_markdown(
        """# Embodiment Profile

## Operational Envelope
### Base Footprint
0.4 m radius.
### Base Mobility
Planar translation and yaw.
### Reachable Workspace
0.2-1.2 m above the floor.

## Perception Configuration
### Sensor Modalities
Color images and base-distance measurements.
### Model-Visible Views
Head and chest.
### Private Calibration
Do not expose this deployment note.

## Base-Relative Positions
### Camera Positions
Head and chest camera positions relative to the base center.
### Initial Gripper Positions
Left and right gripper positions in the initial posture.

## Private Deployment Notes
Do not expose this section.
"""
    )

    rendered = profile.render()

    assert "## Operational Envelope" in rendered
    assert "### Initial Gripper Positions" in rendered
    assert "Private Calibration" not in rendered
    assert "Private Deployment Notes" not in rendered
    assert "Do not expose" not in rendered


def test_embodiment_profile_reports_missing_paper_items() -> None:
    profile = EmbodimentProfile.from_markdown(
        "## Operational Envelope\n### Base Footprint\n0.4 m radius.",
        validate=False,
    )

    assert "missing item content: Operational Envelope / Base Mobility" in (
        profile.validation_errors()
    )
    with pytest.raises(ValueError, match="Perception Configuration"):
        profile.validate()


def test_default_run_log_uses_user_state_not_working_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_dir = tmp_path / "checkout"
    home = tmp_path / "home"
    working_dir.mkdir()
    home.mkdir()
    monkeypatch.chdir(working_dir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("THEA_HARNESS_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    expected_dir = home / ".local" / "state" / "thea-harness" / "runs"
    assert default_log_dir() == expected_dir

    with run_logger() as (path, log):
        log({"type": "done"})

    assert path.parent == expected_dir
    assert path.is_file()
    assert not (working_dir / "output").exists()


def test_run_log_directory_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "runtime-logs"
    monkeypatch.setenv("THEA_HARNESS_LOG_DIR", str(configured))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "ignored-xdg"))

    assert default_log_dir() == configured


def test_default_run_log_honors_xdg_state_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_home = tmp_path / "xdg-state"
    monkeypatch.delenv("THEA_HARNESS_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    assert default_log_dir() == (state_home / "thea-harness" / "runs")


def test_explicit_run_log_path_remains_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = tmp_path / "explicit" / "run.jsonl"
    monkeypatch.setenv(
        "THEA_HARNESS_LOG_DIR",
        str(tmp_path / "environment-default"),
    )

    with run_logger(explicit) as (path, log):
        log({"type": "done"})

    assert path == explicit
    assert explicit.is_file()


def test_relative_xdg_state_home_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    working_dir = tmp_path / "working"
    home.mkdir()
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("THEA_HARNESS_LOG_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")

    assert default_log_dir() == (home / ".local" / "state" / "thea-harness" / "runs")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"embodiment_profile_file": True}, "embodiment_profile_file"),
        ({"skills": {"dir": ""}}, "skills.dir"),
        ({"paths": {"base_dir": 3}}, "paths.base_dir"),
        (
            {
                "servers": [
                    {"name": "robot", "command": "one"},
                    {"name": "robot", "command": "two"},
                ]
            },
            "duplicate server name",
        ),
        (
            {"servers": [{"command": "robot", "call_timeout_sec": 0}]},
            "call_timeout_sec",
        ),
        (
            {
                "servers": [
                    {
                        "command": "robot",
                        "timeout_argument_margin_sec": -1,
                    }
                ]
            },
            "timeout_argument_margin_sec",
        ),
    ],
)
def test_runtime_config_rejects_invalid_public_paths_and_servers(
    config: dict,
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        validate_runtime_config(config)
