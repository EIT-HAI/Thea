from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPOSITORY_ROOT / "install.sh"
RUN_SCRIPT = REPOSITORY_ROOT / "run.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is unavailable")
def test_public_install_and_run_scripts_have_valid_shell_syntax() -> None:
    for script in (INSTALL_SCRIPT, RUN_SCRIPT):
        assert script.stat().st_mode & stat.S_IXUSR

    subprocess.run(
        ["bash", "-n", str(INSTALL_SCRIPT), str(RUN_SCRIPT)],
        check=True,
        cwd=REPOSITORY_ROOT,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is unavailable")
def test_run_script_cli_preflight_requires_no_lark_credentials(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "servers: []\n"
        "llm:\n"
        "  provider: openrouter\n"
        "  api_key_env: OPENROUTER_API_KEY\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=test-key\n", encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "LARK_ALLOWED_OPEN_IDS",
            "LARK_APP_ID",
            "LARK_APP_SECRET",
            "LLM_PROVIDER",
            "OPENROUTER_API_KEY",
        }
    }
    venv = tmp_path / "harness-only-venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(Path(sys.executable))
    environment["THEA_VENV"] = str(venv)

    completed = subprocess.run(
        [
            str(RUN_SCRIPT),
            "--mode",
            "cli",
            "--check",
            "--config",
            str(config),
            "--env",
            str(env_file),
        ],
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Preflight passed: provider=openrouter, mode=cli" in completed.stdout


def test_lark_process_loads_the_selected_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / "deployment.env"
    env_file.write_text("THEA_ENV_PROBE=selected\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items() if key != "THEA_ENV_PROBE"
    }
    environment["THEA_ENV"] = str(env_file)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; import thea_lark.server; "
                "print(os.environ.get('THEA_ENV_PROBE', ''))"
            ),
        ],
        check=True,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "selected"


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is unavailable")
def test_run_script_preflight_uses_installed_public_packages(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "servers: []\nllm:\n  provider: anthropic\n  api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "THEA_VENV": str(Path(sys.executable).parent.parent),
        "THEA_CONFIG": str(config),
        "THEA_LARK_TRANSPORT": "ws",
        "LARK_APP_ID": "cli_test",
        "LARK_APP_SECRET": "test-secret",
        "LARK_ALLOWED_OPEN_IDS": "ou_test",
        "ANTHROPIC_API_KEY": "test-api-key",
    }

    completed = subprocess.run(
        [str(RUN_SCRIPT), "--check"],
        check=True,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert "Preflight passed: transport=ws, provider=anthropic" in completed.stdout
    assert "mode=channel" in completed.stdout

    override_environment = {
        **environment,
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "test-openai-key",
    }
    overridden = subprocess.run(
        [str(RUN_SCRIPT), "--check", "--provider=openai"],
        check=True,
        cwd=REPOSITORY_ROOT,
        env=override_environment,
        capture_output=True,
        text=True,
    )

    assert "Preflight passed: transport=ws, provider=openai" in overridden.stdout

    launched = subprocess.run(
        [str(RUN_SCRIPT), "--help"],
        check=True,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert "Usage: ./run.sh --mode MODE" in launched.stdout
    assert "simulation" in launched.stdout
    assert "robot" in launched.stdout
    assert "--harness-factory" in launched.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is unavailable")
def test_run_script_preflights_simulation_and_robot_factories(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "servers: []\nllm:\n  provider: anthropic\n  api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )
    factory = tmp_path / "deployment_factory.py"
    factory.write_text(
        "def create_harness(config, session_context):\n"
        "    raise AssertionError('preflight must not create deployment resources')\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(
                None,
                (str(tmp_path), os.environ.get("PYTHONPATH", "")),
            )
        ),
        "THEA_VENV": str(Path(sys.executable).parent.parent),
        "THEA_CONFIG": str(config),
        "LARK_APP_ID": "cli_test",
        "LARK_APP_SECRET": "test-secret",
        "LARK_ALLOWED_OPEN_IDS": "ou_test",
        "ANTHROPIC_API_KEY": "test-api-key",
    }

    for mode in ("simulation", "robot"):
        completed = subprocess.run(
            [
                str(RUN_SCRIPT),
                "--check",
                "--mode",
                mode,
                "--harness-factory",
                "deployment_factory:create_harness",
            ],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert f"mode={mode}" in completed.stdout

    missing_factory = subprocess.run(
        [str(RUN_SCRIPT), "--check", "--mode", "simulation"],
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert missing_factory.returncode != 0
    assert "required for simulation mode" in missing_factory.stderr
