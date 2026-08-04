"""Outbound Lark delivery and Harness run presentation."""

from __future__ import annotations

import sys
from typing import Any

from harness import Harness

from .adapter import ParsedMessage
from .application_state import ApplicationState
from .delivery import (
    send_card,
    send_final_image_attachments,
    send_streaming_final,
    send_text_reply,
    send_uploaded_image_fallbacks,
    upload_final_card_images,
)
from .image_transport import (
    _load_query_user_image,
    _query_user_image_filename,
)
from .presentation import _RunTimingStats
from .run_coordinator import (
    FinalVisualEvidence,
    RunCoordinator,
    StreamingRunState,
    record_stream_event,
    stream_harness,
    terminal_stream_result,
)
from .user_interaction import (
    collect_query_user_visuals,
    resolve_observation_view_visuals,
    send_image_attachments,
    upload_card_images,
)

PATCH_THROTTLE_S = 1.5


class ChannelDelivery:
    """Translate Harness events and visual evidence into Lark messages."""

    def __init__(self, state: ApplicationState, *, max_images: int) -> None:
        self._state = state
        self.max_images = max_images

    def final_visual_evidence(self) -> FinalVisualEvidence:
        return FinalVisualEvidence(max_images=self.max_images)

    def collect_query_user_visuals(
        self,
        *,
        observation_visuals: list[dict[str, Any]] | None = None,
        image_urls: list[str] | str | None = None,
        image_paths: list[str] | str | None = None,
        visual_outputs: list[dict[str, Any]] | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return collect_query_user_visuals(
            max_images=self.max_images,
            observation_visuals=observation_visuals,
            image_urls=image_urls,
            image_paths=image_paths,
            visual_outputs=visual_outputs,
        )

    def resolve_observation_view_visuals(
        self,
        session,
        observation_views: list[str] | str | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return resolve_observation_view_visuals(
            session,
            observation_views,
            max_images=self.max_images,
        )

    async def send_query_user_payload(
        self,
        open_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        sent = await self._state.client().send_text(open_id, prompt)
        msg_id = (sent.get("data") or {}).get("message_id", "")
        print(
            f"[query_user] sent prompt to {open_id} message_id={msg_id!r} "
            f"attachments={len(attachments)}",
            file=sys.stderr,
        )
        await self.send_image_attachments(
            open_id,
            attachments,
            context="query_user",
        )

    async def send_image_attachments(
        self,
        receive_id: str,
        attachments: list[dict[str, Any]],
        *,
        context: str,
        receive_id_type: str = "open_id",
    ) -> None:
        await send_image_attachments(
            receive_id,
            attachments,
            context=context,
            receive_id_type=receive_id_type,
            client_factory=self._state.client,
            load_image=_load_query_user_image,
            image_filename=_query_user_image_filename,
        )

    async def upload_card_images(
        self,
        attachments: list[dict[str, Any]],
        *,
        context: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        return await upload_card_images(
            attachments,
            context=context,
            client_factory=self._state.client,
            load_image=_load_query_user_image,
            image_filename=_query_user_image_filename,
        )

    async def fetch_images(self, parsed: ParsedMessage) -> list[bytes]:
        """Download images keyed by one normalized Lark message."""
        if not parsed.image_keys:
            return []
        message_id = ((parsed.raw.get("event") or {}).get("message") or {}).get(
            "message_id", ""
        )
        if not message_id:
            return []
        images: list[bytes] = []
        for key in parsed.image_keys:
            try:
                images.append(
                    await self._state.client().download_resource(message_id, key)
                )
            except Exception as exc:
                print(
                    f"[lark] image fetch {key!r} failed: {exc}",
                    file=sys.stderr,
                )
        return images

    async def send_text(self, parsed: ParsedMessage, text: str) -> None:
        await send_text_reply(parsed, text, client_factory=self._state.client)

    async def send_initial_card(
        self,
        parsed: ParsedMessage,
        card: dict,
    ) -> str | None:
        return await send_card(
            parsed,
            card,
            client_factory=self._state.client,
            failure_label="send card",
        )

    async def send_final_card(
        self,
        parsed: ParsedMessage,
        card: dict,
    ) -> str | None:
        return await send_card(
            parsed,
            card,
            client_factory=self._state.client,
            failure_label="send final card",
        )

    async def upload_final_card_images(
        self,
        attachments: list[dict[str, Any]],
        timings: _RunTimingStats,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return await upload_final_card_images(
            attachments,
            timings,
            upload_images=self.upload_card_images,
        )

    async def send_streaming_final(
        self,
        parsed: ParsedMessage,
        *,
        final: str,
        ok: bool,
        attachments: list[dict[str, Any]],
        card_images: list[dict[str, str]],
        timings: _RunTimingStats,
    ) -> None:
        await send_streaming_final(
            parsed,
            final=final,
            ok=ok,
            attachments=attachments,
            card_images=card_images,
            timings=timings,
            send_final_card=self.send_final_card,
            send_text=self.send_text,
            send_uploaded_images=self.send_uploaded_image_fallbacks,
            send_final_attachments=self.send_final_image_attachments,
        )

    async def send_uploaded_image_fallbacks(
        self,
        parsed: ParsedMessage,
        card_images: list[dict[str, str]],
        timings: _RunTimingStats,
    ) -> None:
        await send_uploaded_image_fallbacks(
            parsed,
            card_images,
            timings,
            client_factory=self._state.client,
        )

    async def send_final_image_attachments(
        self,
        parsed: ParsedMessage,
        attachments: list[dict[str, Any]],
        timings: _RunTimingStats,
    ) -> None:
        await send_final_image_attachments(
            parsed,
            attachments,
            timings,
            send_images=self.send_image_attachments,
        )

    def run_coordinator(self) -> RunCoordinator:
        return RunCoordinator(
            max_images=self.max_images,
            patch_throttle_s=PATCH_THROTTLE_S,
            stream_harness=stream_harness,
            send_text=self.send_text,
            send_initial_card=self.send_initial_card,
            upload_card_images=self.upload_final_card_images,
            send_final_attachments=self.send_final_image_attachments,
            send_streaming_final=self.send_streaming_final,
            client_factory=self._state.client,
        )

    async def run_streaming(
        self,
        parsed: ParsedMessage,
        harness: Harness,
        log,
        **kwargs: Any,
    ) -> None:
        await self.run_coordinator().run_streaming(
            parsed,
            harness,
            log,
            **kwargs,
        )

    async def run_raw(
        self,
        parsed: ParsedMessage,
        harness: Harness,
        log,
        **kwargs: Any,
    ) -> None:
        await self.run_coordinator().run_raw(
            parsed,
            harness,
            log,
            **kwargs,
        )


__all__ = [
    "ChannelDelivery",
    "PATCH_THROTTLE_S",
    "StreamingRunState",
    "_RunTimingStats",
    "record_stream_event",
    "terminal_stream_result",
]
