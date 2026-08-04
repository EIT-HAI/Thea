"""FastAPI composition root for the Thea Lark channel."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .application_state import ApplicationState
from .channel_delivery import ChannelDelivery
from .channel_dispatch import ChannelDispatcher
from .session_runtime import QUERY_USER_MAX_IMAGES, SessionRuntime

load_dotenv(os.environ.get("THEA_ENV") or ".env")

state = ApplicationState()
delivery = ChannelDelivery(state, max_images=QUERY_USER_MAX_IMAGES)
sessions = SessionRuntime(state, delivery)
dispatcher = ChannelDispatcher(state, sessions, delivery)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage channel, session, and optional WebSocket resources."""
    sessions.configured_deployment_max_concurrency()
    sessions.configured_max_sessions()
    try:
        await state.start(dispatcher.run_harness_and_reply)
        yield
    finally:
        await sessions.close_all()
        await state.close()


app = FastAPI(title="Thea Lark channel", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Return a minimal liveness response for the channel process."""
    return {"ok": True, "channel": "lark"}


@app.post("/webhook/lark")
async def webhook(request: Request) -> Any:
    """Authenticate one webhook event and enqueue its normalized message."""
    adapter = state.adapter
    if not adapter.webhook_auth_configured:
        return JSONResponse(
            {"error": "webhook authentication is not configured"},
            status_code=503,
        )
    body = await request.body()
    if not adapter.verify(dict(request.headers), body):
        return JSONResponse({"error": "signature failed"}, status_code=401)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    if "encrypt" in payload:
        try:
            payload = adapter.unwrap_encrypted(payload["encrypt"])
        except Exception as exc:
            print(f"[webhook] decrypt failed: {exc}", file=sys.stderr)
            return JSONResponse({"error": "decrypt failed"}, status_code=400)

    if not adapter.verify_payload_token(payload):
        return JSONResponse({"error": "verification token failed"}, status_code=401)
    if (challenge := adapter.handle_url_verification(payload)) is not None:
        return challenge

    parsed = adapter.parse(payload)
    if parsed is None:
        message = (payload.get("event") or {}).get("message") or {}
        print(
            "[webhook] parse returned None; "
            f"message_type={message.get('message_type')!r} "
            f"chat_type={message.get('chat_type')!r}",
            file=sys.stderr,
        )
        return {"ok": True, "ignored": True}

    print(
        f"[webhook] dispatching task: user={parsed.user_id} "
        f"text={parsed.text!r} n_images={len(parsed.image_keys)}",
        file=sys.stderr,
    )
    task = asyncio.create_task(dispatcher.run_harness_and_reply(parsed))
    task.add_done_callback(_log_task_exception)
    return {"ok": True, "user": parsed.user_id}


def _log_task_exception(task: asyncio.Task) -> None:
    """Surface background exceptions that would otherwise be dropped."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    import traceback

    print(
        f"[lark] background task crashed: {type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--transport",
        choices=["webhook", "ws"],
        default="webhook",
        help=(
            "webhook: Lark pushes events to the configured HTTPS endpoint; "
            "ws: this process opens a long-lived connection to Lark without "
            "requiring a public callback URL."
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "LLM provider: "
            "mock|anthropic|openai|openrouter|mimo|qwen|deepseek|ollama. "
            "Defaults to $LLM_PROVIDER and then the YAML configuration."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Trusted Harness YAML file. Defaults to $LARK_HARNESS_CONFIG, "
            "then lark/config.example.yaml."
        ),
    )
    parser.add_argument(
        "--harness-factory",
        default=None,
        help=(
            "Optional deployment composition factory in module:callable form. "
            "Defaults to $LARK_HARNESS_FACTORY."
        ),
    )
    return parser


def main() -> None:
    """Run the Lark channel using webhook or WebSocket inbound transport."""
    import uvicorn

    args = _argument_parser().parse_args()
    state.configure(
        provider_override=args.provider or os.environ.get("LLM_PROVIDER"),
        config_path=args.config,
        harness_factory=args.harness_factory,
        transport=args.transport,
    )

    if state.transport == "ws":
        print(
            f"Lark WebSocket inbound  ·  health: "
            f"http://{args.host}:{args.port}/health  "
            f"(provider: {state.provider_override or 'yaml-default'}; "
            "Ctrl+C to stop)",
            flush=True,
        )
    else:
        print(
            f"Lark webhook → http://{args.host}:{args.port}/webhook/lark  "
            f"(provider: {state.provider_override or 'yaml-default'}; "
            "Ctrl+C to stop)",
            flush=True,
        )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
