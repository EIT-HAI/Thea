"""Harness stream consumption and Lark result delivery."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from harness import Harness

from .adapter import ParsedMessage
from .presentation import (
    _clean_final_text_for_lark,
    _event_brief,
    _RunTimingStats,
    _thinking_card,
)
from .user_interaction import collect_query_user_visuals

StreamHarness = Callable[..., AsyncIterator[dict[str, Any]]]
SendText = Callable[[ParsedMessage, str], Awaitable[None]]
SendCard = Callable[[ParsedMessage, dict[str, Any]], Awaitable[str | None]]
UploadCardImages = Callable[
    [list[dict[str, Any]], _RunTimingStats],
    Awaitable[tuple[list[dict[str, Any]], list[dict[str, str]]]],
]
SendAttachments = Callable[
    [ParsedMessage, list[dict[str, Any]], _RunTimingStats],
    Awaitable[None],
]
SendStreamingFinal = Callable[..., Awaitable[None]]


async def stream_harness(
    harness: Harness,
    instruction: str,
    log: Callable[[dict[str, Any]], None],
    *,
    max_turns: int = 100,
    failure_budget: int = 20,
    images: list[bytes] | None = None,
    poll_replan: Any = None,
    loaded_skill: dict[str, Any] | None = None,
    on_event: Any = None,
    should_cancel: Any = None,
) -> AsyncIterator[dict[str, Any]]:
    """Bridge the synchronous Harness event generator into asyncio."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def producer() -> None:
        try:
            for event in harness.run_stream(
                instruction,
                max_turns=max_turns,
                failure_budget=failure_budget,
                images=images,
                poll_replan=poll_replan,
                should_cancel=should_cancel,
                loaded_skill=loaded_skill,
            ):
                if on_event is not None:
                    on_event(event)
                asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(
                queue.put(
                    {
                        "type": "error",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                ),
                loop,
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop)

    threading.Thread(target=producer, daemon=True).start()
    while True:
        event = await queue.get()
        if event is sentinel:
            return
        log(event)
        yield event


def visual_artifacts_to_attachments(
    visual_artifacts: Any,
) -> list[dict[str, str]]:
    """Convert evaluator artifacts into final-card attachments."""
    if not isinstance(visual_artifacts, dict):
        return []
    images = visual_artifacts.get("images")
    if not isinstance(images, list):
        return []
    attachments: list[dict[str, str]] = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            continue
        source = str(image.get("path") or image.get("image_path") or "")
        if not source:
            continue
        camera = str(image.get("camera") or image.get("id") or f"visual_{index}")
        sha = str(image.get("sha256") or "")
        caption = f"evaluate_run evidence: {camera}"
        if sha:
            caption = f"{caption} {sha[:6]}"
        attachments.append({"kind": "path", "source": source, "caption": caption})
    return attachments


@dataclass
class FinalVisualEvidence:
    """Retain only bounded, unique image evidence for the final response."""

    max_images: int = 8
    attachments: list[dict[str, Any]] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)

    def record(self, event: dict[str, Any]) -> None:
        sources: list[dict[str, Any]] = [event]
        result = event.get("result")
        if isinstance(result, dict):
            sources.append(result)
        for source in sources:
            candidates = collect_query_user_visuals(
                max_images=self.max_images,
                visual_outputs=source.get("visual_outputs"),
            )
            candidates.extend(
                visual_artifacts_to_attachments(source.get("visual_artifacts"))
            )
            for item in candidates:
                key = (str(item.get("kind") or ""), str(item.get("source") or ""))
                if not key[1] or key in self._seen:
                    continue
                self._seen.add(key)
                if len(self.attachments) < self.max_images:
                    self.attachments.append(item)


@dataclass
class StreamingRunState:
    """Mutable presentation state for one streaming Lark response."""

    final_visual_evidence: FinalVisualEvidence
    last_patch_at: float = 0.0
    turn: int = 0
    last_brief: str = ""
    live_events: list[str] = field(default_factory=list)
    sent_intermediate_say: set[tuple[int, str]] = field(default_factory=set)
    final: str = "[harness: no output]"
    ok: bool = True


def record_stream_event(
    state: StreamingRunState,
    event: dict[str, Any],
    timings: _RunTimingStats,
) -> None:
    """Update presentation state from one Harness event."""
    timings.record(event)
    state.final_visual_evidence.record(event)
    event_type = str(event.get("type") or "")
    if "turn" in event:
        state.turn = int(event["turn"])
    brief = _event_brief(event)
    if not brief:
        return
    state.last_brief = brief
    label = (
        "Final answer"
        if event_type in {"say", "speak"} and not event.get("has_tool_call")
        else f"Turn {state.turn}"
    )
    line = f"**{label}** · {brief}"
    if not state.live_events or state.live_events[-1] != line:
        state.live_events.append(line)


def terminal_stream_result(
    event: dict[str, Any],
    default_final: str,
) -> tuple[str, bool] | None:
    """Return only a true task boundary or an unrecoverable stream error."""
    event_type = str(event.get("type") or "")
    if event_type == "done":
        ok = event.get("task_status") == "completed" or (
            not event.get("task_status")
            and event.get("terminated_normally") is not False
        )
        return str(event.get("final_text") or default_final), ok
    if event_type == "error":
        return f"[harness error] {event.get('reason', '?')}", False
    return None


class RunCoordinator:
    """Consume one Harness task and deliver its complete Lark presentation."""

    def __init__(
        self,
        *,
        max_images: int,
        patch_throttle_s: float,
        stream_harness: StreamHarness,
        send_text: SendText,
        send_initial_card: SendCard,
        upload_card_images: UploadCardImages,
        send_final_attachments: SendAttachments,
        send_streaming_final: SendStreamingFinal,
        client_factory: Callable[[], Any],
    ) -> None:
        self.max_images = max_images
        self.patch_throttle_s = patch_throttle_s
        self.stream_harness = stream_harness
        self.send_text = send_text
        self.send_initial_card = send_initial_card
        self.upload_card_images = upload_card_images
        self.send_final_attachments = send_final_attachments
        self.send_streaming_final = send_streaming_final
        self.client_factory = client_factory

    async def run_streaming(
        self,
        parsed: ParsedMessage,
        harness: Harness,
        log: Callable[[dict[str, Any]], None],
        **run_options: Any,
    ) -> None:
        """Stream progress into a live card, then send the terminal response."""
        started_at = time.monotonic()
        timings = _RunTimingStats()
        card_started_at = time.monotonic()
        card_message_id = await self.send_initial_card(parsed, _thinking_card(0))
        timings.record_lark(
            "send_initial_card",
            (time.monotonic() - card_started_at) * 1000,
        )
        if card_message_id is None:
            await self.run_raw(
                parsed,
                harness,
                log,
                started_at=started_at,
                timings=timings,
                **run_options,
            )
            return

        state = StreamingRunState(
            final_visual_evidence=FinalVisualEvidence(self.max_images)
        )
        images = run_options.get("images")
        instruction = (
            parsed.text or "Analyze this image and respond based on its evidence."
            if images
            else parsed.text
        )
        async for event in self.stream_harness(
            harness,
            instruction,
            log,
            **self._stream_options(run_options),
        ):
            record_stream_event(state, event, timings)
            await self._send_intermediate_say(parsed, state, event, timings)
            terminal = terminal_stream_result(event, state.final)
            if terminal is not None:
                state.final, state.ok = terminal
                break
            await self._maybe_patch_card(
                card_message_id,
                state,
                event,
                timings,
            )

        final = _clean_final_text_for_lark(state.final)
        attachments, card_images = await self.upload_card_images(
            state.final_visual_evidence.attachments,
            timings,
        )
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log(
            {
                "type": "run_timing",
                "elapsed_ms": elapsed_ms,
                "timings": timings.as_dict(elapsed_ms),
            }
        )
        await self.send_streaming_final(
            parsed,
            final=final,
            ok=state.ok,
            attachments=attachments,
            card_images=card_images,
            timings=timings,
        )

    async def run_raw(
        self,
        parsed: ParsedMessage,
        harness: Harness,
        log: Callable[[dict[str, Any]], None],
        *,
        started_at: float | None = None,
        timings: _RunTimingStats | None = None,
        **run_options: Any,
    ) -> None:
        """Run to completion and deliver plain text plus final visual evidence."""
        started_at = started_at or time.monotonic()
        timings = timings or _RunTimingStats()
        final = "[harness: no output]"
        evidence = FinalVisualEvidence(self.max_images)
        sent_say: set[tuple[int, str]] = set()
        images = run_options.get("images")
        instruction = (
            parsed.text or "Analyze this image and respond based on its evidence."
            if images
            else parsed.text
        )
        async for event in self.stream_harness(
            harness,
            instruction,
            log,
            **self._stream_options(run_options),
        ):
            timings.record(event)
            evidence.record(event)
            await self._send_raw_intermediate_say(
                parsed,
                event,
                sent_say,
                timings,
            )
            event_type = event.get("type", "")
            if event_type == "done":
                final = event.get("final_text") or final
            elif event_type in {"turn_budget_exhausted", "failure_budget_exhausted"}:
                final = (
                    "[harness: failure limit reached; task incomplete]"
                    if event_type == "failure_budget_exhausted"
                    else "[harness: maximum turns reached; task incomplete]"
                )
            elif event_type == "error":
                final = f"[harness error] {event.get('reason', '?')}"
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log(
            {
                "type": "run_timing",
                "elapsed_ms": elapsed_ms,
                "timings": timings.as_dict(elapsed_ms),
            }
        )
        send_started_at = time.monotonic()
        await self.send_text(parsed, _clean_final_text_for_lark(final))
        timings.record_lark(
            "send_raw_final",
            (time.monotonic() - send_started_at) * 1000,
        )
        await self.send_final_attachments(parsed, evidence.attachments, timings)

    @staticmethod
    def _stream_options(options: dict[str, Any]) -> dict[str, Any]:
        return {
            key: options.get(key)
            for key in (
                "images",
                "poll_replan",
                "loaded_skill",
                "on_event",
                "should_cancel",
            )
        }

    async def _send_intermediate_say(
        self,
        parsed: ParsedMessage,
        state: StreamingRunState,
        event: dict[str, Any],
        timings: _RunTimingStats,
    ) -> None:
        if event.get("type") not in {"say", "speak"} or not event.get("has_tool_call"):
            return
        text = str(event.get("text") or "").strip()
        key = (int(event.get("turn") or state.turn or 0), text)
        if not text or key in state.sent_intermediate_say:
            return
        started_at = time.monotonic()
        await self.send_text(parsed, text)
        timings.record_lark(
            "send_intermediate_say",
            (time.monotonic() - started_at) * 1000,
        )
        state.sent_intermediate_say.add(key)

    async def _send_raw_intermediate_say(
        self,
        parsed: ParsedMessage,
        event: dict[str, Any],
        sent: set[tuple[int, str]],
        timings: _RunTimingStats,
    ) -> None:
        if event.get("type") not in {"say", "speak"} or not event.get("has_tool_call"):
            return
        text = str(event.get("text") or "").strip()
        key = (int(event.get("turn") or 0), text)
        if not text or key in sent:
            return
        started_at = time.monotonic()
        await self.send_text(parsed, text)
        timings.record_lark(
            "send_intermediate_say",
            (time.monotonic() - started_at) * 1000,
        )
        sent.add(key)

    async def _maybe_patch_card(
        self,
        message_id: str,
        state: StreamingRunState,
        event: dict[str, Any],
        timings: _RunTimingStats,
    ) -> None:
        now = time.monotonic()
        force = event.get("type") == "tool_call" and event.get("name") == "query_user"
        if (
            not (force or now - state.last_patch_at >= self.patch_throttle_s)
            or not state.last_brief
        ):
            return
        try:
            started_at = time.monotonic()
            await self.client_factory().patch_card(
                message_id,
                _thinking_card(state.turn, state.last_brief, state.live_events),
            )
            timings.record_lark(
                "patch_query_user_card" if force else "patch_thinking_card",
                (time.monotonic() - started_at) * 1000,
            )
            state.last_patch_at = now
        except Exception as exc:
            print(
                f"[lark] patch card failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
