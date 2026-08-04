"""Process-owned state for the Lark channel application."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .adapter import LarkAdapter, ParsedMessage
from .client import LarkClient


class ApplicationState:
    """Own channel configuration, the HTTP client, and WebSocket transport."""

    def __init__(self) -> None:
        self.provider_override: str | None = None
        self.config_path: str | None = None
        self.harness_factory: str | None = None
        self.transport = "webhook"
        self.adapter = LarkAdapter()
        self._client: LarkClient | None = None
        self._ws_runtime: Any = None

    def configure(
        self,
        *,
        provider_override: str | None,
        config_path: str | None,
        harness_factory: str | None,
        transport: str,
    ) -> None:
        """Apply trusted command-line configuration before application startup."""
        self.provider_override = provider_override
        self.config_path = config_path
        self.harness_factory = harness_factory
        self.transport = transport

    async def start(
        self,
        dispatch: Callable[[ParsedMessage], Awaitable[None]],
    ) -> None:
        """Open process-owned channel resources."""
        self._client = LarkClient()
        if self.transport != "ws":
            return
        from .ws import LarkWSRuntime

        self._ws_runtime = LarkWSRuntime(self.adapter, dispatch)
        await self._ws_runtime.start()

    async def close(self) -> None:
        """Close process-owned channel resources."""
        if self._client is not None:
            await self._client.close()
        self._client = None
        self._ws_runtime = None

    def client(self) -> LarkClient:
        """Return the client owned by the active application lifespan."""
        if self._client is None:
            raise RuntimeError("Lark client is not initialized.")
        return self._client

    @staticmethod
    def next_run_log_path() -> Path:
        """Return a channel-owned path under the platform state directory."""
        import os

        configured = os.environ.get("LARK_RUN_LOG_DIR", "").strip()
        if configured:
            directory = Path(configured).expanduser()
        else:
            configured_state_home = os.environ.get("XDG_STATE_HOME", "").strip()
            candidate = Path(configured_state_home).expanduser()
            state_home = (
                candidate
                if configured_state_home and candidate.is_absolute()
                else Path.home() / ".local" / "state"
            )
            directory = state_home / "thea-lark" / "runs"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return directory / f"run_{timestamp}-{time.time_ns()}.jsonl"


__all__ = ["ApplicationState"]
