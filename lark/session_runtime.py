"""Per-user Harness session lifecycle for the Lark channel."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from harness import Harness

from .application_state import ApplicationState
from .channel_delivery import ChannelDelivery
from .runtime import (
    ChannelSessionContext,
    apply_provider_override,
    create_channel_harness,
    load_runtime_config,
)
from .sessions import (
    DeploymentSemaphore,
    Session,
    SessionManager,
    instrument_model_for_usage,
    positive_env_int,
)
from .user_interaction import install_notify_user, install_query_user


def _bounded_env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        print(f"[lark] invalid {name}; using {default}", file=sys.stderr)
        value = default
    value = max(minimum, value)
    return min(maximum, value) if maximum is not None else value


SESSION_TTL_SEC = _bounded_env_int(
    "LARK_SESSION_TTL_SEC",
    21600,
    minimum=60,
)
QUERY_USER_TIMEOUT_SEC = _bounded_env_int(
    "LARK_QUERY_USER_TIMEOUT_SEC",
    300,
    minimum=5,
)
QUERY_USER_MAX_IMAGES = _bounded_env_int(
    "LARK_QUERY_USER_MAX_IMAGES",
    8,
    minimum=1,
    maximum=20,
)
QUERY_USER_CANCEL_SENTINEL = "__USER_CANCELLED__"


class SessionRuntime:
    """Own Session instances and deployment-wide task serialization."""

    def __init__(
        self,
        state: ApplicationState,
        delivery: ChannelDelivery,
    ) -> None:
        self._state = state
        self._delivery = delivery
        self._manager = SessionManager(
            ttl_sec=SESSION_TTL_SEC,
            cancel_sentinel=QUERY_USER_CANCEL_SENTINEL,
        )
        self._deployment_semaphore = DeploymentSemaphore()

    @property
    def sessions(self) -> dict[str, Session]:
        return self._manager.sessions

    @property
    def mutex(self) -> asyncio.Lock:
        return self._manager.mutex

    @property
    def creation_tasks(self):
        return self._manager.creation_tasks

    @staticmethod
    def configured_max_sessions() -> int:
        return positive_env_int("LARK_MAX_SESSIONS", 64)

    @staticmethod
    def configured_deployment_max_concurrency() -> int:
        return positive_env_int("LARK_DEPLOYMENT_MAX_CONCURRENCY", 1)

    def deployment_task_semaphore(self) -> asyncio.Semaphore:
        return self._deployment_semaphore.get(
            self.configured_deployment_max_concurrency()
        )

    async def get_or_create(
        self,
        channel_context: ChannelSessionContext,
    ) -> tuple[Session, bool]:
        return await self._manager.get_or_create(
            channel_context,
            max_sessions=self.configured_max_sessions(),
            compose=self.compose,
        )

    async def compose(self, channel_context: ChannelSessionContext) -> Session:
        """Compose one Harness-backed Session outside the global session mutex."""
        open_id = channel_context.user_id
        harness: Harness | None = None
        try:
            config = load_runtime_config(self._state.config_path)
            apply_provider_override(config, self._state.provider_override)
            harness = await asyncio.to_thread(
                create_channel_harness,
                config,
                session_context=channel_context,
                factory_path=self._state.harness_factory,
            )
            session = Session(
                harness=harness,
                channel_context=channel_context,
                last_used=time.time(),
                lock=asyncio.Lock(),
                menu_state=None,
            )
            instrument_model_for_usage(session)
            loop = asyncio.get_running_loop()
            install_query_user(
                session,
                open_id,
                loop,
                timeout_sec=QUERY_USER_TIMEOUT_SEC,
                cancel_sentinel=QUERY_USER_CANCEL_SENTINEL,
                max_images=QUERY_USER_MAX_IMAGES,
                send_payload=self._delivery.send_query_user_payload,
            )
            install_notify_user(
                session,
                open_id,
                loop,
                client_factory=self._state.client,
            )
            return session
        except BaseException:
            if harness is not None:
                try:
                    await asyncio.to_thread(harness.close)
                except Exception:
                    print(
                        f"[session] cleanup failed for {open_id}",
                        file=sys.stderr,
                    )
            raise

    async def close_all(self) -> None:
        await self._manager.close_all()

    async def cancel_and_close(
        self,
        session: Session,
        *,
        lock_already_held: bool = False,
    ) -> None:
        await self._manager.cancel_and_close(
            session,
            lock_already_held=lock_already_held,
        )


__all__ = [
    "QUERY_USER_CANCEL_SENTINEL",
    "QUERY_USER_MAX_IMAGES",
    "QUERY_USER_TIMEOUT_SEC",
    "SESSION_TTL_SEC",
    "SessionRuntime",
]
