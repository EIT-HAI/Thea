"""Message routing and Harness task dispatch for the Lark channel."""

from __future__ import annotations

import os
import sys
from typing import Any

from harness.observability import run_logger

from .adapter import ParsedMessage
from .application_state import ApplicationState
from .channel_delivery import ChannelDelivery
from .commands import CommandRouter
from .menu import TopMenuState, render_top_menu
from .message_routing import (
    queue_replan_message as route_replan_message,
)
from .message_routing import (
    resolve_pending_query_message as resolve_query_message,
)
from .message_routing import (
    run_model_task as execute_model_task,
)
from .message_routing import (
    run_model_task_serialized as execute_model_task_serialized,
)
from .runtime import ChannelSessionContext
from .session_runtime import QUERY_USER_CANCEL_SENTINEL, SESSION_TTL_SEC, SessionRuntime
from .sessions import (
    Session,
    pop_pending_replan,
    queue_replan,
    should_queue_replan,
)
from .user_interaction import capture_session_observation_event


class ChannelDispatcher:
    """Route one normalized message through session and Harness boundaries."""

    def __init__(
        self,
        state: ApplicationState,
        sessions: SessionRuntime,
        delivery: ChannelDelivery,
    ) -> None:
        self._state = state
        self._sessions = sessions
        self._delivery = delivery

    def command_router(self) -> CommandRouter:
        return CommandRouter(
            sessions=self._sessions.sessions,
            sessions_mutex=self._sessions.mutex,
            session_ttl_sec=SESSION_TTL_SEC,
            send_text=self._delivery.send_text,
            run_model_task=self.run_model_task,
            fetch_images=self._delivery.fetch_images,
            close_session=self._sessions.cancel_and_close,
            return_to_menu=self.return_to_menu,
        )

    async def return_to_menu(
        self,
        session: Session,
        parsed: ParsedMessage,
        *,
        separator: bool = False,
    ) -> None:
        session.menu_state = TopMenuState()
        prefix = "\n———\n" if separator else ""
        await self._delivery.send_text(parsed, prefix + render_top_menu())

    async def run_model_task(
        self,
        session: Session,
        parsed: ParsedMessage,
        *,
        images: list[bytes] | None = None,
    ) -> None:
        await execute_model_task(
            session,
            parsed,
            images=images,
            deployment_semaphore=self._sessions.deployment_task_semaphore(),
            run_serialized=self.run_model_task_serialized,
            pop_replan=pop_pending_replan,
        )

    async def run_model_task_serialized(
        self,
        session: Session,
        parsed: ParsedMessage,
        *,
        images: list[bytes] | None = None,
        should_cancel: Any = None,
    ) -> None:
        mode = os.environ.get("LARK_RENDERING_MODE", "auto").strip().lower()
        runner = (
            self._delivery.run_raw if mode == "raw" else self._delivery.run_streaming
        )
        print(f"[harness] runner={runner.__name__} mode={mode}", file=sys.stderr)
        await execute_model_task_serialized(
            session,
            parsed,
            images=images,
            should_cancel=should_cancel,
            runner=runner,
            run_logger_factory=run_logger,
            log_path=self._state.next_run_log_path(),
            pop_replan=pop_pending_replan,
            capture_observation=capture_session_observation_event,
        )

    async def resolve_pending_query_message(self, parsed: ParsedMessage) -> bool:
        return await resolve_query_message(
            parsed,
            sessions=self._sessions.sessions,
            cancel_sentinel=QUERY_USER_CANCEL_SENTINEL,
            send_text=self._delivery.send_text,
        )

    async def acquire_user_session(
        self,
        parsed: ParsedMessage,
    ) -> tuple[Session, bool] | None:
        try:
            session, is_new = await self._sessions.get_or_create(
                ChannelSessionContext(
                    user_id=parsed.user_id,
                    chat_id=parsed.chat_id,
                    transport=self._state.transport,
                )
            )
        except Exception as exc:
            print(
                f"[session] acquire failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            await self._delivery.send_text(
                parsed,
                f"[Startup failed] {type(exc).__name__}: {exc}",
            )
            return None
        print(
            f"[harness] session ready (continuation={session.harness.ctx is not None},"
            f" is_new={is_new}, menu_state={type(session.menu_state).__name__})",
            file=sys.stderr,
        )
        return session, is_new

    async def queue_replan_message(
        self,
        parsed: ParsedMessage,
        session: Session,
    ) -> bool:
        return await route_replan_message(
            parsed,
            session,
            should_queue=should_queue_replan,
            queue=queue_replan,
            send_text=self._delivery.send_text,
        )

    async def dispatch_session_message(
        self,
        parsed: ParsedMessage,
        session: Session,
        *,
        is_new: bool,
    ) -> None:
        async with session.lock:
            router = self.command_router()
            if (
                is_new
                and session.menu_state is not None
                and not parsed.text.strip().startswith("/")
            ):
                await self._delivery.send_text(parsed, render_top_menu())
                return
            if await router.try_handle_tool_command(parsed, session):
                return
            if session.menu_state is not None:
                await router.handle_menu_input(parsed, session)
                return

            images = await self._delivery.fetch_images(parsed)
            print(
                f"[harness] images fetched: {len(images)} (keys={parsed.image_keys})",
                file=sys.stderr,
            )
            await self.run_model_task(session, parsed, images=images)

    async def run_harness_and_reply(self, parsed: ParsedMessage) -> None:
        print(f"[harness] starting for user={parsed.user_id}", file=sys.stderr)
        if await self.resolve_pending_query_message(parsed):
            return
        if await self.command_router().try_handle_admin_command(parsed):
            return
        acquired = await self.acquire_user_session(parsed)
        if acquired is None:
            return
        session, is_new = acquired
        if await self.queue_replan_message(parsed, session):
            return
        await self.dispatch_session_message(parsed, session, is_new=is_new)


__all__ = ["ChannelDispatcher"]
