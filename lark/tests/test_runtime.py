from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harness import Harness
from thea_lark import server, session_runtime
from thea_lark.adapter import ParsedMessage
from thea_lark.runtime import (
    CONFIG_ENV,
    DEFAULT_CONFIG_PATH,
    ChannelSessionContext,
    apply_provider_override,
    create_channel_harness,
    load_runtime_config,
    register_channel_fallback_tools,
)
from thea_lark.sessions import queue_replan
from thea_lark.user_interaction import build_query_user_prompt
from thea_lark.ws import LarkWSRuntime


def test_load_runtime_config_from_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text(
        "llm:\n  provider: mock\n  replay_path: replay.jsonl\n",
        encoding="utf-8",
    )

    config = load_runtime_config(path)

    assert config["llm"]["provider"] == "mock"
    assert config["servers"] == []


def test_load_runtime_config_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text("servers: []\nllm: {}\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV, str(path))

    assert load_runtime_config() == {"servers": [], "llm": {}}


def test_packaged_config_enables_accumulated_context_compaction() -> None:
    config = load_runtime_config(DEFAULT_CONFIG_PATH)

    assert config["context"]["compaction"]["enabled"] is True
    assert config["context"]["compaction"]["keep_recent_turns"] == 4


def test_provider_override_discards_stale_provider_fields() -> None:
    config = {
        "llm": {
            "provider": "anthropic",
            "model": "claude",
            "api_key_env": "ANTHROPIC_API_KEY",
            "max_tokens": 123,
        }
    }

    returned = apply_provider_override(config, "openai")

    assert returned is config
    assert config["llm"] == {"provider": "openai", "max_tokens": 123}


def test_websocket_dispatch_failures_are_observable(capsys) -> None:
    future: concurrent.futures.Future[None] = concurrent.futures.Future()
    future.set_exception(RuntimeError("dispatch broke"))

    LarkWSRuntime._report_dispatch_completion(future)

    assert (
        "[lark-ws] dispatch failed: RuntimeError: dispatch broke"
        in capsys.readouterr().err
    )


def test_default_assembly_registers_channel_fallbacks(tmp_path: Path) -> None:
    replay = tmp_path / "replay.jsonl"
    replay.write_text(
        json.dumps({"text": "Done.", "tool_calls": []}) + "\n",
        encoding="utf-8",
    )
    harness = create_channel_harness(
        {
            "servers": [],
            "llm": {"provider": "mock", "replay_path": str(replay)},
            "context": {"compaction": {"enabled": False}},
        },
        session_context=ChannelSessionContext(
            user_id="ou_test",
            chat_id="oc_test",
            transport="webhook",
        ),
    )
    try:
        assert isinstance(harness, Harness)
        assert harness.registry.has("query_user")
        assert harness.registry.has("notify_user")
        query_definition = next(
            item
            for item in harness.registry.list_tool_definitions()
            if item["name"] == "query_user"
        )
        query_properties = query_definition["inputSchema"]["properties"]
        assert "image_paths" not in query_properties
        assert "image_urls" not in query_properties
        assert query_properties["candidate_refs"]["items"]["type"] == "string"

        query_result = harness.registry.call_tool(
            "query_user",
            {"question": "Which object?"},
        )
        notify_result = harness.registry.call_tool(
            "notify_user",
            {"message": "Working.", "notification_type": "progress"},
        )
        assert query_result["kind"] == "user_channel_unavailable"
        assert notify_result["kind"] == "user_channel_unavailable"
    finally:
        harness.close()


def test_query_user_prompt_renders_candidate_refs_and_current_views() -> None:
    prompt = build_query_user_prompt(
        "Which bottle do you mean?",
        [
            {
                "kind": "url",
                "source": "https://example.com/front.png",
                "caption": "front",
            }
        ],
        candidate_refs=["bottle_12", "bottle_18", "bottle_12"],
    )

    assert "Candidate Scene Graph refs:" in prompt
    assert "1. bottle_12" in prompt
    assert "2. bottle_18" in prompt
    assert prompt.count("bottle_12") == 1
    assert "Current Observation views attached:" in prompt
    assert "Reply with a candidate ref" in prompt


def test_deployment_factory_receives_public_session_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = tmp_path / "replay.jsonl"
    replay.write_text(
        json.dumps({"text": "Done.", "tool_calls": []}) + "\n",
        encoding="utf-8",
    )
    context = ChannelSessionContext(
        user_id="ou_public",
        chat_id="oc_public",
        transport="ws",
    )
    received: dict[str, Any] = {}

    def factory(
        config: dict[str, Any],
        session_context: ChannelSessionContext,
    ) -> Harness:
        received["config"] = config
        received["context"] = session_context
        return Harness(config, builtin_registrar=register_channel_fallback_tools)

    monkeypatch.setattr("thea_lark.runtime._load_factory", lambda _path: factory)
    harness = create_channel_harness(
        {
            "servers": [],
            "llm": {"provider": "mock", "replay_path": str(replay)},
            "context": {"compaction": {"enabled": False}},
        },
        session_context=context,
        factory_path="deployment:factory",
    )
    try:
        assert received["context"] is context
        assert received["config"]["llm"]["provider"] == "mock"
    finally:
        harness.close()


def test_deployment_guard_serializes_tasks_across_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_DEPLOYMENT_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("LARK_RUN_LOG_DIR", str(tmp_path))
    active = 0
    maximum_active = 0
    both_started = 0

    async def runner(
        _parsed: ParsedMessage,
        _harness: Any,
        _log: Any,
        **_kwargs: Any,
    ) -> None:
        nonlocal active, both_started, maximum_active
        both_started += 1
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    monkeypatch.setattr(server.delivery, "run_streaming", runner)
    monkeypatch.setenv("LARK_RENDERING_MODE", "streaming")

    def session(user: str) -> Any:
        return SimpleNamespace(
            harness=object(),
            channel_context=ChannelSessionContext(user, f"chat-{user}", "webhook"),
            pending_loaded_skill=None,
            pending_replans=[],
            replan_lock=threading.Lock(),
            usage=None,
            last_used=0.0,
            menu_state=None,
        )

    async def exercise() -> None:
        await asyncio.gather(
            server.dispatcher.run_model_task(
                session("one"),
                ParsedMessage(user_id="one", text="first", raw={}),
            ),
            server.dispatcher.run_model_task(
                session("two"),
                ParsedMessage(user_id="two", text="second", raw={}),
            ),
        )

    asyncio.run(exercise())

    assert both_started == 2
    assert maximum_active == 1


def test_query_user_reply_resolves_without_waiting_for_deployment_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending: concurrent.futures.Future[str] = concurrent.futures.Future()
    session = SimpleNamespace(pending_query=pending)

    async def send_text_reply(_parsed: ParsedMessage, _text: str) -> None:
        return None

    monkeypatch.setattr(server.delivery, "send_text", send_text_reply)
    server.sessions.sessions["reply-user"] = session
    try:
        resolved = asyncio.run(
            server.dispatcher.resolve_pending_query_message(
                ParsedMessage(user_id="reply-user", text="the left one", raw={})
            )
        )
    finally:
        server.sessions.sessions.pop("reply-user", None)

    assert resolved
    assert pending.result() == "the left one"
    assert session.pending_query is None


def test_completed_query_future_is_cleared_without_a_second_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending: concurrent.futures.Future[str] = concurrent.futures.Future()
    pending.set_result("already answered")
    session = SimpleNamespace(pending_query=pending)
    replies: list[str] = []

    async def send_text_reply(_parsed: ParsedMessage, text: str) -> None:
        replies.append(text)

    monkeypatch.setattr(server.delivery, "send_text", send_text_reply)
    server.sessions.sessions["reply-user"] = session
    try:
        resolved = asyncio.run(
            server.dispatcher.resolve_pending_query_message(
                ParsedMessage(user_id="reply-user", text="duplicate", raw={})
            )
        )
    finally:
        server.sessions.sessions.pop("reply-user", None)

    assert not resolved
    assert session.pending_query is None
    assert replies == []


def test_group_final_card_replies_in_originating_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    class Client:
        async def reply_card(self, message_id: str, card: dict[str, Any]) -> dict:
            calls.append(("reply_card", message_id, card))
            return {"data": {"message_id": "om_final"}}

        async def send_card(self, open_id: str, card: dict[str, Any]) -> dict:
            calls.append(("send_card", open_id, card))
            return {"data": {"message_id": "om_private"}}

    monkeypatch.setattr(server.state, "client", lambda: Client())
    parsed = ParsedMessage(
        user_id="ou_sender",
        chat_id="oc_group",
        text="run",
        raw={
            "event": {
                "message": {
                    "chat_type": "group",
                    "message_id": "om_origin",
                }
            }
        },
    )

    message_id = asyncio.run(server.delivery.send_final_card(parsed, {"type": "final"}))

    assert message_id == "om_final"
    assert calls == [("reply_card", "om_origin", {"type": "final"})]


def test_run_model_task_passes_cooperative_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[bool] = []
    session = SimpleNamespace(
        cancel_event=None,
        pending_replans=[],
        replan_lock=threading.Lock(),
    )

    async def serialized(
        received_session: Any,
        _parsed: ParsedMessage,
        *,
        images: list[bytes],
        should_cancel: Any,
    ) -> None:
        assert received_session is session
        assert images == [b"image"]
        observed.append(should_cancel())
        received_session.cancel_event.set()
        observed.append(should_cancel())

    monkeypatch.setattr(
        server.dispatcher,
        "run_model_task_serialized",
        serialized,
    )
    monkeypatch.setenv("LARK_DEPLOYMENT_MAX_CONCURRENCY", "1")

    asyncio.run(
        server.dispatcher.run_model_task(
            session,
            ParsedMessage(user_id="ou_cancel", text="run", raw={}),
            images=[b"image"],
        )
    )

    assert observed == [False, True]


def test_unconsumed_replan_starts_a_new_task_after_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instructions: list[str] = []
    session = SimpleNamespace(
        cancel_event=None,
        pending_replans=[],
        replan_lock=threading.Lock(),
        last_used=0.0,
    )

    async def serialized(
        received_session: Any,
        parsed: ParsedMessage,
        *,
        images: list[bytes],
        should_cancel: Any,
    ) -> None:
        assert received_session is session
        assert not should_cancel()
        instructions.append(parsed.text)
        if len(instructions) == 1:
            queue_replan(session, "second instruction")

    monkeypatch.setattr(
        server.dispatcher,
        "run_model_task_serialized",
        serialized,
    )
    monkeypatch.setenv("LARK_DEPLOYMENT_MAX_CONCURRENCY", "1")

    asyncio.run(
        server.dispatcher.run_model_task(
            session,
            ParsedMessage(user_id="ou_replan", text="first instruction", raw={}),
        )
    )

    assert instructions == ["first instruction", "second instruction"]
    assert session.pending_replans == []


def test_cancellation_discards_unconsumed_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instructions: list[str] = []
    session = SimpleNamespace(
        cancel_event=None,
        pending_replans=[],
        replan_lock=threading.Lock(),
        last_used=0.0,
    )

    async def serialized(
        received_session: Any,
        parsed: ParsedMessage,
        *,
        images: list[bytes],
        should_cancel: Any,
    ) -> None:
        instructions.append(parsed.text)
        queue_replan(received_session, "must not run")
        received_session.cancel_event.set()
        assert should_cancel()

    monkeypatch.setattr(
        server.dispatcher,
        "run_model_task_serialized",
        serialized,
    )
    monkeypatch.setenv("LARK_DEPLOYMENT_MAX_CONCURRENCY", "1")

    asyncio.run(
        server.dispatcher.run_model_task(
            session,
            ParsedMessage(user_id="ou_cancel", text="first instruction", raw={}),
        )
    )

    assert instructions == ["first instruction"]
    assert session.pending_replans == []


def test_session_limit_rejects_a_new_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Registry:
        def list_tool_definitions(self) -> list[dict[str, Any]]:
            return []

    class FakeHarness:
        model = object()
        registry = Registry()

        def close(self) -> None:
            return None

    monkeypatch.setenv("LARK_MAX_SESSIONS", "1")
    monkeypatch.setattr(
        session_runtime,
        "load_runtime_config",
        lambda _path: {"servers": []},
    )
    monkeypatch.setattr(
        session_runtime,
        "create_channel_harness",
        lambda *_args, **_kwargs: FakeHarness(),
    )

    async def exercise() -> None:
        server.sessions.sessions.clear()
        try:
            await server.sessions.get_or_create(
                ChannelSessionContext("ou_one", "oc_one", "webhook")
            )
            with pytest.raises(RuntimeError, match="session limit"):
                await server.sessions.get_or_create(
                    ChannelSessionContext("ou_two", "oc_two", "webhook")
                )
        finally:
            server.sessions.sessions.clear()

    asyncio.run(exercise())


def test_session_composition_does_not_hold_the_global_session_mutex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()

    class FakeHarness:
        model = object()

        def close(self) -> None:
            return None

    def create(*_args, **_kwargs):
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with counter_lock:
            active -= 1
        return FakeHarness()

    monkeypatch.setenv("LARK_MAX_SESSIONS", "4")
    monkeypatch.setattr(
        session_runtime,
        "load_runtime_config",
        lambda _path: {"servers": []},
    )
    monkeypatch.setattr(session_runtime, "create_channel_harness", create)
    monkeypatch.setattr(
        session_runtime,
        "instrument_model_for_usage",
        lambda _session: False,
    )
    monkeypatch.setattr(
        session_runtime,
        "install_query_user",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        session_runtime,
        "install_notify_user",
        lambda *_args, **_kwargs: None,
    )

    async def exercise() -> None:
        server.sessions.sessions.clear()
        server.sessions.creation_tasks.clear()
        try:
            await asyncio.gather(
                server.sessions.get_or_create(
                    ChannelSessionContext("ou_one", "oc_one", "webhook")
                ),
                server.sessions.get_or_create(
                    ChannelSessionContext("ou_two", "oc_two", "webhook")
                ),
            )
        finally:
            await server.sessions.close_all()

    asyncio.run(exercise())

    assert maximum_active == 2


def test_concurrent_messages_share_one_session_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compositions = 0

    class FakeHarness:
        model = object()

        def close(self) -> None:
            return None

    def create(*_args, **_kwargs):
        nonlocal compositions
        compositions += 1
        time.sleep(0.03)
        return FakeHarness()

    monkeypatch.setenv("LARK_MAX_SESSIONS", "2")
    monkeypatch.setattr(
        session_runtime,
        "load_runtime_config",
        lambda _path: {"servers": []},
    )
    monkeypatch.setattr(session_runtime, "create_channel_harness", create)
    monkeypatch.setattr(
        session_runtime,
        "instrument_model_for_usage",
        lambda _session: False,
    )
    monkeypatch.setattr(
        session_runtime,
        "install_query_user",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        session_runtime,
        "install_notify_user",
        lambda *_args, **_kwargs: None,
    )

    async def exercise() -> None:
        server.sessions.sessions.clear()
        server.sessions.creation_tasks.clear()
        try:
            first, second = await asyncio.gather(
                server.sessions.get_or_create(
                    ChannelSessionContext("ou_shared", "oc_one", "webhook")
                ),
                server.sessions.get_or_create(
                    ChannelSessionContext("ou_shared", "oc_two", "webhook")
                ),
            )
            assert first[0] is second[0]
            assert (first[1], second[1]) == (True, False)
        finally:
            await server.sessions.close_all()

    asyncio.run(exercise())

    assert compositions == 1


@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_deployment_concurrency_requires_positive_integer(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_DEPLOYMENT_MAX_CONCURRENCY", value)

    with pytest.raises(ValueError, match="positive integer"):
        server.sessions.configured_deployment_max_concurrency()
