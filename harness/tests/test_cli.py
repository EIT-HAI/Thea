from __future__ import annotations

import io
from pathlib import Path

from harness import ToolRegistry, cli
from harness.terminal.styling import THEA_THEME
from rich.console import Console


def test_terminal_interaction_registers_live_user_tools() -> None:
    output = io.StringIO()
    interaction = cli.TerminalInteraction(
        input_fn=lambda _prompt: "desk_24",
        output=output,
    )
    registry = ToolRegistry()
    interaction.register_tools(registry)

    query = registry.call_tool(
        "query_user",
        {
            "question": "Which desk?",
            "candidate_refs": ["desk_12", "desk_24"],
        },
    )
    notification = registry.call_tool(
        "notify_user",
        {"message": "Starting now.", "notification_type": "progress"},
    )

    assert query["success"] is True
    assert query["answer"] == "desk_24"
    assert notification["success"] is True
    rendered = output.getvalue()
    assert "Which desk?" in rendered
    assert "desk_12" in rendered
    assert "Progress" in rendered
    assert "Starting now." in rendered


def test_interactive_terminal_runs_tasks_and_resets_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeHarness:
        def __init__(self) -> None:
            self.instructions: list[str] = []
            self.reset_count = 0

        def run_stream(self, instruction, **_options):
            self.instructions.append(instruction)
            yield {
                "type": "model_response",
                "turn": 1,
                "text": "Done.",
                "reasoning_content": "Use the current instruction.",
                "tool_call_count": 0,
            }
            yield {
                "type": "done",
                "turns": 1,
                "task_status": "completed",
                "termination_reason": "model_returned_no_tool_call",
                "final_text": "Done.",
            }

        def reset_session(self) -> None:
            self.reset_count += 1

    answers = iter(["Explain the current context.", "/reset", "/quit"])
    output = io.StringIO()
    harness = FakeHarness()
    monkeypatch.setenv("THEA_HARNESS_LOG_DIR", str(tmp_path / "logs"))

    status = cli.run_interactive(
        harness,  # type: ignore[arg-type]
        input_fn=lambda _prompt: next(answers),
        renderer=cli.TerminalRenderer(output),
        output=output,
    )

    assert status == 0
    assert harness.instructions == ["Explain the current context."]
    assert harness.reset_count == 1
    assert "Use the current instruction." in output.getvalue()
    assert "Thea · Final" in output.getvalue()
    assert "Done." in output.getvalue()
    assert len(list((tmp_path / "logs").glob("*.jsonl"))) == 1


def test_terminal_renderer_uses_ansi_highlighting_on_a_tty() -> None:
    output = io.StringIO()
    console = Console(
        file=output,
        theme=THEA_THEME,
        force_terminal=True,
        color_system="standard",
        width=88,
    )
    renderer = cli.TerminalRenderer(console=console)

    renderer.render(
        {
            "type": "model_response",
            "turn": 1,
            "reasoning_content": "Inspect current evidence.",
        }
    )
    renderer.render(
        {
            "type": "tool_call",
            "turn": 1,
            "name": "notify_user",
            "arguments": {"message": "Working."},
        }
    )
    renderer.render(
        {
            "type": "done",
            "task_status": "completed",
            "final_text": "Finished.",
        }
    )

    rendered = output.getvalue()
    assert "\x1b[" in rendered
    assert "Reasoning" in rendered
    assert "notify_user" in rendered
    assert "Thea · Final" in rendered


def test_cli_one_shot_uses_configured_provider_without_lark(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "servers: []\n"
        "llm:\n"
        "  provider: openrouter\n"
        "  api_key_env: OPENROUTER_API_KEY\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=test-key\n", encoding="utf-8")
    observed: dict[str, object] = {}

    class FakeHarness:
        def __init__(self, runtime_config, *, builtin_registrar):
            observed["config"] = runtime_config
            registry = ToolRegistry()
            builtin_registrar(registry)
            observed["tools"] = registry.names()

        def run_stream(self, instruction, **_options):
            observed["instruction"] = instruction
            yield {
                "type": "done",
                "turns": 1,
                "task_status": "completed",
                "termination_reason": "model_returned_no_tool_call",
                "final_text": "Connected.",
            }

        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setattr(cli, "Harness", FakeHarness)
    monkeypatch.setenv("THEA_HARNESS_LOG_DIR", str(tmp_path / "logs"))

    status = cli.main(
        [
            "--config",
            str(config),
            "--env",
            str(env),
            "--instruction",
            "Confirm the model connection.",
        ]
    )

    assert status == 0
    assert observed["instruction"] == "Confirm the model connection."
    assert observed["tools"] == {"notify_user", "query_user"}
    assert observed["closed"] is True
    assert observed["config"]["llm"]["provider"] == "openrouter"  # type: ignore[index]


def test_cli_rejects_mock_provider(tmp_path: Path, capsys, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "servers: []\nllm:\n  provider: mock\n  replay_path: replay.jsonl\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    status = cli.main(["--config", str(config), "--check"])

    assert status == 2
    assert "requires a real model provider" in capsys.readouterr().err
