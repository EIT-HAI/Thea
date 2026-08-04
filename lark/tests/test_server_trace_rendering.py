from __future__ import annotations

import asyncio

import pytest
from thea_lark import channel_delivery as delivery_module
from thea_lark import server
from thea_lark.adapter import ParsedMessage
from thea_lark.presentation import _event_brief, _RunTimingStats
from thea_lark.run_coordinator import FinalVisualEvidence, terminal_stream_result


def test_final_visual_evidence_keeps_only_bounded_unique_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = FinalVisualEvidence(max_images=2)

    evidence.record(
        {
            "type": "tool_result",
            "visual_outputs": [{"url": "https://example.test/top.png"}],
            "result": {
                "visual_outputs": [
                    {"url": "https://example.test/top.png"},
                    {"url": "https://example.test/result.png"},
                    {"url": "https://example.test/ignored.png"},
                ]
            },
        }
    )
    evidence.record({"type": "model_response", "reasoning_content": "not retained"})

    assert [(item["kind"], item["source"]) for item in evidence.attachments] == [
        ("url", "https://example.test/top.png"),
        ("url", "https://example.test/result.png"),
    ]
    assert set(vars(evidence)) == {"max_images", "attachments", "_seen"}


@pytest.mark.parametrize(
    "event_type",
    ["turn_budget_exhausted", "failure_budget_exhausted"],
)
def test_budget_events_do_not_end_stream_before_done(event_type: str) -> None:
    assert (
        terminal_stream_result(
            {"type": event_type, "final_text": "not yet final"},
            "fallback",
        )
        is None
    )


def test_final_image_upload_returns_only_failed_attachments_for_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachments = [
        {"kind": "url", "source": "https://example.test/ok.png"},
        {"kind": "url", "source": "https://example.test/failed.png"},
    ]

    async def upload(
        received: list[dict],
        *,
        context: str,
    ) -> tuple[list[dict[str, str]], list[dict]]:
        assert received == attachments
        assert context == "final_card"
        return (
            [{"img_key": "img_ok", "alt": "ok"}],
            [attachments[1]],
        )

    monkeypatch.setattr(server.delivery, "upload_card_images", upload)

    failed, card_images = asyncio.run(
        server.delivery.upload_final_card_images(
            attachments,
            _RunTimingStats(),
        )
    )

    assert failed == [attachments[1]]
    assert card_images == [{"img_key": "img_ok", "alt": "ok"}]


def test_streaming_final_sends_partial_upload_failures_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = [{"kind": "url", "source": "https://example.test/failed.png"}]
    sent: list[list[dict]] = []

    async def send_card(_parsed, _card):
        return "message-id"

    async def send_attachments(_parsed, attachments, _timings):
        sent.append(list(attachments))

    monkeypatch.setattr(server.delivery, "send_final_card", send_card)
    monkeypatch.setattr(
        server.delivery,
        "send_final_image_attachments",
        send_attachments,
    )

    asyncio.run(
        server.delivery.send_streaming_final(
            ParsedMessage(user_id="user", text="", raw={}),
            final="Done.",
            ok=True,
            attachments=failed,
            card_images=[{"img_key": "uploaded", "alt": "uploaded"}],
            timings=_RunTimingStats(),
        )
    )

    assert sent == [failed]


def test_streaming_final_preserves_uploaded_images_when_card_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_text: list[str] = []
    sent_images: list[list[dict[str, str]]] = []

    async def send_card(_parsed, _card):
        raise RuntimeError("card unavailable")

    async def send_text(_parsed, text):
        sent_text.append(text)

    async def send_uploaded(_parsed, images, _timings):
        sent_images.append(list(images))

    monkeypatch.setattr(server.delivery, "send_final_card", send_card)
    monkeypatch.setattr(server.delivery, "send_text", send_text)
    monkeypatch.setattr(
        server.delivery,
        "send_uploaded_image_fallbacks",
        send_uploaded,
    )

    asyncio.run(
        server.delivery.send_streaming_final(
            ParsedMessage(user_id="user", text="", raw={}),
            final="Done.",
            ok=True,
            attachments=[],
            card_images=[{"img_key": "uploaded", "alt": "view"}],
            timings=_RunTimingStats(),
        )
    )

    assert sent_text == ["Done."]
    assert sent_images == [[{"img_key": "uploaded", "alt": "view"}]]


def test_raw_mode_preserves_final_visual_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment = {"kind": "url", "source": "https://example.test/final.png"}
    sent: list[list[dict]] = []

    async def stream(*_args, **_kwargs):
        yield {
            "type": "tool_result",
            "result": {"visual_outputs": [{"url": attachment["source"]}]},
        }
        yield {"type": "done", "final_text": "Done.", "task_status": "completed"}

    async def send_text(_parsed, _text):
        return None

    async def send_attachments(_parsed, attachments, _timings):
        sent.append(list(attachments))

    monkeypatch.setattr(delivery_module, "stream_harness", stream)
    monkeypatch.setattr(server.delivery, "send_text", send_text)
    monkeypatch.setattr(
        server.delivery,
        "send_final_image_attachments",
        send_attachments,
    )

    asyncio.run(
        server.delivery.run_raw(
            ParsedMessage(user_id="user", text="task", raw={}),
            object(),
            lambda _event: None,
        )
    )

    assert sent == [[{**attachment, "caption": ""}]]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {
                "type": "model_response",
                "reasoning_content": "reason",
                "decision_summary": "decision",
            },
            "💭 **Think**\nreason\n🧭 **Decision**\ndecision",
        ),
        ({"type": "model_response"}, "💭 thinking..."),
        ({"type": "say", "text": "line one\nline two"}, "🗣 line one line two"),
        (
            {"type": "skill_loaded", "name": "inspect"},
            "🧩 Loaded skill `inspect` for this task",
        ),
        (
            {
                "type": "tool_call",
                "name": "query_user",
                "arguments": {"question": "Continue?"},
            },
            "❓ Awaiting your confirmation: Continue?",
        ),
        (
            {
                "type": "tool_call",
                "name": "notify_user",
                "arguments": {"message": "Working"},
            },
            "🔔 User notification: Working",
        ),
        (
            {"type": "tool_call", "name": "move", "arguments": {"distance": 1}},
            '🔧 `move({"distance": 1})`',
        ),
        (
            {
                "type": "tool_result",
                "name": "notify_user",
                "result": {
                    "kind": "notify_user_sent",
                    "notification_type": "milestone",
                },
            },
            "✓ Notification sent (milestone)",
        ),
        (
            {
                "type": "tool_result",
                "success": False,
                "result": {
                    "reason": "blocked",
                    "visual_outputs": [{"url": "https://example.test/image.png"}],
                },
            },
            "✗ blocked 🖼",
        ),
        (
            {"type": "replan_requested", "text": "next\nstep"},
            "↻ Current skill completed; replanning from revised instruction: next step",
        ),
        ({"type": "unknown"}, ""),
    ],
)
def test_event_brief_preserves_event_specific_status(
    event: dict, expected: str
) -> None:
    assert _event_brief(event) == expected
