from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from thea_lark import server
from thea_lark.adapter import ParsedMessage
from thea_lark.commands import build_help_text
from thea_lark.user_interaction import capture_session_observation_event


class _Registry:
    def has(self, name: str) -> bool:
        return name == "move_base"

    def list_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "move_base",
                "description": "Move the base.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "distance_m": {"type": "number"},
                    },
                    "required": ["distance_m"],
                    "additionalProperties": False,
                },
            }
        ]

    def call_tool(self, _name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("slash command bypassed the Agentic Loop")


class _Harness:
    def __init__(self) -> None:
        self.registry = _Registry()

    def model_visible_tool_definitions(self) -> list[dict[str, Any]]:
        return self.registry.list_tool_definitions()


def test_physical_tool_slash_routes_through_agentic_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def run_model_task(
        session: Any, parsed: ParsedMessage, **kwargs: Any
    ) -> None:
        captured["session"] = session
        captured["instruction"] = parsed.text
        captured["kwargs"] = kwargs

    async def fetch_images(_parsed: ParsedMessage) -> list[bytes]:
        return []

    monkeypatch.setattr(server.dispatcher, "run_model_task", run_model_task)
    monkeypatch.setattr(server.delivery, "fetch_images", fetch_images)

    session = SimpleNamespace(harness=_Harness())
    parsed = ParsedMessage(
        user_id="user",
        text="/move_base distance_m=0.5",
        raw={},
    )

    handled = asyncio.run(
        server.dispatcher.command_router().try_handle_tool_command(parsed, session)
    )

    assert handled
    assert "normal Agentic Loop" in captured["instruction"]
    assert '"distance_m": 0.5' in captured["instruction"]


def test_physical_tool_slash_reports_missing_arguments_before_agentic_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[str] = []

    async def send_text_reply(_parsed: ParsedMessage, text: str) -> None:
        replies.append(text)

    async def run_model_task(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("incomplete command reached the Agentic Loop")

    monkeypatch.setattr(server.delivery, "send_text", send_text_reply)
    monkeypatch.setattr(server.dispatcher, "run_model_task", run_model_task)

    session = SimpleNamespace(harness=_Harness())
    parsed = ParsedMessage(user_id="user", text="/move_base", raw={})

    handled = asyncio.run(
        server.dispatcher.command_router().try_handle_tool_command(parsed, session)
    )

    assert handled
    assert replies and replies[0].startswith("Missing parameters: distance_m")


def test_skill_command_loads_metadata_then_runs_task_through_agentic_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class SkillRegistry:
        def has(self, name: str) -> bool:
            return name == "load_skill"

        def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            captured["skill_call"] = (name, arguments)
            return {
                "success": True,
                "name": "inspect",
                "description": "Inspect the scene.",
                "instructions": "Observe before acting.",
                "resources": ["guide.md"],
            }

    async def send_text_reply(_parsed: ParsedMessage, text: str) -> None:
        captured["reply"] = text

    async def fetch_images(_parsed: ParsedMessage) -> list[bytes]:
        return [b"image"]

    async def run_model_task(
        _session: Any,
        parsed: ParsedMessage,
        **kwargs: Any,
    ) -> None:
        captured["task"] = parsed.text
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server.delivery, "send_text", send_text_reply)
    monkeypatch.setattr(server.delivery, "fetch_images", fetch_images)
    monkeypatch.setattr(server.dispatcher, "run_model_task", run_model_task)

    session = SimpleNamespace(
        harness=SimpleNamespace(registry=SkillRegistry()),
        pending_loaded_skill=None,
    )
    parsed = ParsedMessage(
        user_id="user",
        text="/skill inspect check the shelf",
        raw={},
    )

    handled = asyncio.run(
        server.dispatcher.command_router().try_handle_tool_command(parsed, session)
    )

    assert handled
    assert captured["skill_call"] == ("load_skill", {"name": "inspect"})
    assert captured["task"] == "check the shelf"
    assert captured["kwargs"] == {"images": [b"image"]}
    assert session.pending_loaded_skill["instructions"] == "Observe before acting."


def test_observation_cache_uses_public_event_visual_outputs() -> None:
    session = SimpleNamespace(latest_observation_visual_outputs=[])
    event = {
        "type": "observation",
        "visual_outputs": [
            {
                "camera": "torso",
                "image_url": {"url": "data:image/png;base64,AA=="},
            }
        ],
    }

    capture_session_observation_event(session, event)
    matches, missing = server.delivery.resolve_observation_view_visuals(
        session,
        ["torso"],
    )

    assert matches == event["visual_outputs"]
    assert missing == []


def test_observation_view_resolution_reports_every_missing_view() -> None:
    front = {
        "view": "front",
        "image_url": {"url": "data:image/png;base64,AA=="},
    }
    session = SimpleNamespace(latest_observation_visual_outputs=[front])

    matches, missing = server.delivery.resolve_observation_view_visuals(
        session,
        ["front", "wrist"],
    )

    assert matches == [front]
    assert missing == ["wrist"]


def test_lark_client_is_lazy_at_module_import() -> None:
    assert server.state._client is None


def test_internal_evaluator_is_not_listed_or_routable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[str] = []

    class Registry(_Registry):
        def has(self, name: str) -> bool:
            return name in {"move_base", "evaluate_run"}

        def list_tool_definitions(self) -> list[dict[str, Any]]:
            return [
                *super().list_tool_definitions(),
                {
                    "name": "evaluate_run",
                    "description": "Internal evaluator.",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]

    class Harness:
        registry = Registry()

        @staticmethod
        def model_visible_tool_definitions() -> list[dict[str, Any]]:
            return [Registry().list_tool_definitions()[0]]

    async def send_text_reply(_parsed: ParsedMessage, text: str) -> None:
        replies.append(text)

    monkeypatch.setattr(server.delivery, "send_text", send_text_reply)
    session = SimpleNamespace(harness=Harness())

    assert "evaluate_run" not in build_help_text(session)
    asyncio.run(
        server.dispatcher.command_router()._handle_registered_tool_command(
            ParsedMessage(user_id="user", text="/evaluate_run", raw={}),
            session,
            "/evaluate_run",
            "evaluate_run",
            "",
        )
    )
    assert replies == [
        "Unknown command /evaluate_run.\n\nUse /help to list available commands."
    ]


def test_run_logs_default_to_xdg_state_directory(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LARK_RUN_LOG_DIR", raising=False)
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    path = server.state.next_run_log_path()

    assert path.parent == state_home / "thea-lark" / "runs"


def test_status_omits_pricing_and_unavailable_token_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[str] = []

    async def send_text_reply(_parsed: ParsedMessage, text: str) -> None:
        replies.append(text)

    harness = SimpleNamespace(
        ctx=None,
        model=SimpleNamespace(model="public-model"),
    )
    server.sessions.sessions["status-user"] = SimpleNamespace(
        harness=harness,
        last_used=time.time(),
        usage=None,
    )
    monkeypatch.setattr(server.delivery, "send_text", send_text_reply)
    try:
        handled = asyncio.run(
            server.dispatcher.command_router().try_handle_admin_command(
                ParsedMessage(user_id="status-user", text="/status", raw={})
            )
        )
    finally:
        server.sessions.sessions.pop("status-user", None)

    assert handled
    assert "public-model" in replies[0]
    assert "token" not in replies[0]
    assert "$" not in replies[0]
