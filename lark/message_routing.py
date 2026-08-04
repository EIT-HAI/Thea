"""Task execution and deferred-message routing for the Lark channel."""

from __future__ import annotations

import concurrent.futures
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .adapter import ParsedMessage

SendText = Callable[[ParsedMessage, str], Awaitable[None]]
TaskRunner = Callable[..., Awaitable[None]]


async def run_model_task(
    session: Any,
    parsed: ParsedMessage,
    *,
    images: list[bytes] | None,
    deployment_semaphore: Any,
    run_serialized: TaskRunner,
    pop_replan: Callable[[Any], str],
) -> None:
    """Run one task and any instruction deferred at its Tool boundaries."""
    cancel_event = threading.Event()
    session.cancel_event = cancel_event
    async with deployment_semaphore:
        current = parsed
        current_images = list(images or [])
        while True:
            await run_serialized(
                session,
                current,
                images=current_images,
                should_cancel=cancel_event.is_set,
            )
            deferred = pop_replan(session)
            if cancel_event.is_set() or not deferred:
                return
            current = ParsedMessage(
                user_id=parsed.user_id,
                text=deferred,
                raw=parsed.raw,
                chat_id=parsed.chat_id,
            )
            current_images = []


async def run_model_task_serialized(
    session: Any,
    parsed: ParsedMessage,
    *,
    images: list[bytes] | None,
    should_cancel: Any,
    runner: TaskRunner,
    run_logger_factory: Callable[
        [Path],
        AbstractContextManager[tuple[Path, Callable[[dict[str, Any]], None]]],
    ],
    log_path: Path,
    pop_replan: Callable[[Any], str],
    capture_observation: Callable[[Any, dict[str, Any]], None],
) -> None:
    """Run one instruction through the Agentic Loop with the session lock held."""
    loaded_skill = session.pending_loaded_skill
    session.pending_loaded_skill = None
    with run_logger_factory(log_path) as (actual_log_path, log):
        await runner(
            parsed,
            session.harness,
            log,
            images=images or [],
            session_usage=session.usage,
            poll_replan=lambda: pop_replan(session),
            should_cancel=should_cancel,
            loaded_skill=loaded_skill,
            on_event=lambda event: capture_observation(session, event),
        )
    print(f"[lark] run log: {actual_log_path}", file=sys.stderr)
    session.last_used = time.time()
    session.menu_state = None


def complete_pending_query(
    session: Any,
    pending: concurrent.futures.Future[str],
    value: str,
) -> bool:
    """Complete one current query exactly once and clear its public slot."""
    if session.pending_query is not pending or pending.done():
        return False
    try:
        pending.set_result(value)
    except concurrent.futures.InvalidStateError:
        return False
    finally:
        if session.pending_query is pending:
            session.pending_query = None
    return True


async def resolve_pending_query_message(
    parsed: ParsedMessage,
    *,
    sessions: dict[str, Any],
    cancel_sentinel: str,
    send_text: SendText,
) -> bool:
    """Resolve a pending ``query_user`` answer before ordinary dispatch."""
    session = sessions.get(parsed.user_id)
    if session is None or session.pending_query is None:
        return False
    pending = session.pending_query
    if pending.done():
        if session.pending_query is pending:
            session.pending_query = None
        return False

    text = parsed.text.strip()
    if text.lower() == "/cancel":
        if not complete_pending_query(session, pending, cancel_sentinel):
            return False
        await send_text(
            parsed,
            "✓ Waiting cancelled. The task will finish its current cleanup.",
        )
        return True
    if text.startswith("/"):
        complete_pending_query(session, pending, cancel_sentinel)
        return False
    if not complete_pending_query(session, pending, text):
        return False
    preview = text[:30] + ("..." if len(text) > 30 else "")
    await send_text(parsed, f'✓ Received "{preview}". Continuing...')
    return True


async def queue_replan_message(
    parsed: ParsedMessage,
    session: Any,
    *,
    should_queue: Callable[[ParsedMessage, Any], bool],
    queue: Callable[[Any, str], int],
    send_text: SendText,
) -> bool:
    """Queue an instruction received while the current physical Tool runs."""
    if not should_queue(parsed, session):
        return False
    queued_count = queue(session, parsed.text)
    preview = parsed.text.strip()[:60]
    if len(parsed.text.strip()) > 60:
        preview += "..."
    suffix = (
        f"\n({queued_count} revised instructions are now queued)"
        if queued_count > 1
        else ""
    )
    await send_text(
        parsed,
        "✓ Revised instruction received. The agent will replan from the latest "
        "scene after the current robot skill finishes:\n"
        f"{preview}{suffix}",
    )
    return True
