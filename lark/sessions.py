"""Process-local Lark session state and concurrency primitives."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from harness import Harness

from .adapter import ParsedMessage
from .runtime import ChannelSessionContext


@dataclass
class Session:
    """One user's Harness, channel identity, and task coordination state."""

    harness: Harness
    channel_context: ChannelSessionContext
    last_used: float
    lock: asyncio.Lock
    usage: dict[str, int] | None = None
    pending_query: Any = None
    pending_replans: list[str] = field(default_factory=list)
    replan_lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    menu_state: Any = None
    pending_loaded_skill: dict[str, Any] | None = None
    latest_observation_visual_outputs: list[dict[str, Any]] = field(
        default_factory=list
    )


def positive_env_int(name: str, default: int) -> int:
    """Read a strictly positive process setting."""
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


class DeploymentSemaphore:
    """Own the Agentic Loop semaphore for the current asyncio loop."""

    def __init__(self) -> None:
        self._semaphore: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._limit: int | None = None

    def get(self, limit: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop or self._limit != limit:
            self._semaphore = asyncio.Semaphore(limit)
            self._loop = loop
            self._limit = limit
        return self._semaphore


ComposeSession = Callable[[ChannelSessionContext], Awaitable[Session]]


class SessionManager:
    """Own process-local Session reuse, single-flight creation, and shutdown."""

    def __init__(self, *, ttl_sec: int, cancel_sentinel: str) -> None:
        self.ttl_sec = ttl_sec
        self.cancel_sentinel = cancel_sentinel
        self.sessions: dict[str, Session] = {}
        self.mutex = asyncio.Lock()
        self.creation_tasks: dict[str, asyncio.Task[Session]] = {}

    async def get_or_create(
        self,
        channel_context: ChannelSessionContext,
        *,
        max_sessions: int,
        compose: ComposeSession,
    ) -> tuple[Session, bool]:
        """Return one reusable Session without serializing slow composition."""
        user_id = channel_context.user_id
        evicted: list[tuple[str, Session]] = []
        capacity_error = False
        async with self.mutex:
            now = time.time()
            for existing_id in list(self.sessions):
                existing = self.sessions[existing_id]
                if (
                    existing_id != user_id
                    and not existing.lock.locked()
                    and now - existing.last_used > self.ttl_sec
                ):
                    evicted.append((existing_id, self.sessions.pop(existing_id)))

            existing = self.sessions.get(user_id)
            if existing is not None and now - existing.last_used <= self.ttl_sec:
                existing.last_used = now
                return existing, False
            if existing is not None:
                if existing.lock.locked():
                    existing.last_used = now
                    return existing, False
                evicted.append((user_id, self.sessions.pop(user_id)))

            creation_task = self.creation_tasks.get(user_id)
            is_new = creation_task is None
            if creation_task is None and (
                len(self.sessions) + len(self.creation_tasks) >= max_sessions
            ):
                capacity_error = True
            elif creation_task is None:
                creation_task = asyncio.create_task(
                    self._compose_and_publish(
                        channel_context,
                        evicted,
                        compose,
                    ),
                    name=f"lark-session-{user_id}",
                )
                self.creation_tasks[user_id] = creation_task

        if capacity_error or not is_new:
            await self._close_evicted(evicted)
        if capacity_error:
            raise RuntimeError(
                "The process-local Lark session limit has been reached. "
                "Expire or reset an existing session before creating another."
            )
        assert creation_task is not None
        session = await asyncio.shield(creation_task)
        session.last_used = time.time()
        return session, is_new

    async def _compose_and_publish(
        self,
        channel_context: ChannelSessionContext,
        evicted: list[tuple[str, Session]],
        compose: ComposeSession,
    ) -> Session:
        user_id = channel_context.user_id
        current_task = asyncio.current_task()
        session: Session | None = None
        try:
            await self._close_evicted(evicted)
            session = await compose(channel_context)
            async with self.mutex:
                existing = self.sessions.get(user_id)
                if existing is None:
                    self.sessions[user_id] = session
                    published = session
                else:
                    published = existing
                if self.creation_tasks.get(user_id) is current_task:
                    self.creation_tasks.pop(user_id, None)
            if published is not session:
                await asyncio.to_thread(session.harness.close)
            print(
                f"[session] created {user_id} (total={len(self.sessions)})",
                file=sys.stderr,
            )
            return published
        finally:
            async with self.mutex:
                if self.creation_tasks.get(user_id) is current_task:
                    self.creation_tasks.pop(user_id, None)

    @staticmethod
    async def _close_evicted(evicted: list[tuple[str, Session]]) -> None:
        for user_id, session in evicted:
            await asyncio.to_thread(session.harness.close)
            print(f"[session] evicted {user_id} (idle)", file=sys.stderr)

    async def close_all(self) -> None:
        """Finish in-flight composition, cancel tasks, and close all Harnesses."""
        async with self.mutex:
            creation_tasks = list(self.creation_tasks.values())
        if creation_tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in creation_tasks),
                return_exceptions=True,
            )
        async with self.mutex:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            session.cancel_event.set()
            pending = session.pending_query
            if pending is not None and not pending.done():
                pending.set_result(self.cancel_sentinel)
        for session in sessions:
            async with session.lock:
                await asyncio.to_thread(session.harness.close)

    async def cancel_and_close(
        self,
        session: Session,
        *,
        lock_already_held: bool = False,
    ) -> None:
        """Cooperatively stop an active task, then close owned resources."""
        session.cancel_event.set()
        pending = session.pending_query
        if pending is not None and not pending.done():
            pending.set_result(self.cancel_sentinel)
        if lock_already_held:
            await asyncio.to_thread(session.harness.close)
            return
        async with session.lock:
            await asyncio.to_thread(session.harness.close)


def instrument_model_for_usage(session: Session) -> bool:
    """Collect token counts when the model exposes Anthropic-style usage."""
    inner = getattr(session.harness.model, "_impl", session.harness.model)
    if (
        not hasattr(inner, "client")
        or not hasattr(inner.client, "messages")
        or getattr(inner, "_thea_usage_instrumented", False)
    ):
        return False
    original_create = inner.client.messages.create
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    session.usage = usage

    def tracked_create(*args: Any, **kwargs: Any) -> Any:
        response = original_create(*args, **kwargs)
        response_usage = getattr(response, "usage", None)
        if response_usage is None:
            return response
        usage["input_tokens"] += getattr(response_usage, "input_tokens", 0) or 0
        usage["output_tokens"] += getattr(response_usage, "output_tokens", 0) or 0
        usage["cache_creation_tokens"] += (
            getattr(response_usage, "cache_creation_input_tokens", 0) or 0
        )
        usage["cache_read_tokens"] += (
            getattr(response_usage, "cache_read_input_tokens", 0) or 0
        )
        return response

    inner.client.messages.create = tracked_create
    inner._thea_usage_instrumented = True
    return True


def should_queue_replan(parsed: ParsedMessage, session: Session) -> bool:
    """Return whether natural language should replan after the current Tool."""
    text = parsed.text.strip()
    if not text or text.startswith("/") or session.pending_query is not None:
        return False
    return session.lock.locked()


def queue_replan(session: Session, text: str) -> int:
    """Append one bounded deferred instruction and return the queue length."""
    text = str(text or "").strip()
    if not text:
        return len(session.pending_replans)
    with session.replan_lock:
        session.pending_replans.append(text)
        session.pending_replans = session.pending_replans[-5:]
        count = len(session.pending_replans)
    session.last_used = time.time()
    return count


def pop_pending_replan(session: Session) -> str:
    """Join and clear all deferred instructions for the next Tool boundary."""
    with session.replan_lock:
        if not session.pending_replans:
            return ""
        replans = list(session.pending_replans)
        session.pending_replans.clear()
    return "\n".join(replans)
