"""Lark WebSocket inbound transport.

The process dials Lark, so this mode does not require cloudflared or a public
IP address. It corresponds to Lark's WebSocket event-subscription mode.

Architecture:
    application event loop (asyncio)
        ├─ FastAPI /health
        └─ LarkWSRuntime.start()
            └─ worker thread: lark.ws.Client.start() (blocking)
                └─ event callback (on the WebSocket thread)
                    └─ asyncio.run_coroutine_threadsafe(
                          dispatcher(parsed), application_loop)

LarkAdapter.parse() is shared because ``lark.JSON.marshal(event)`` produces
the same nested header/event/sender/message schema as webhook payloads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
from collections.abc import Awaitable, Callable

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from .adapter import LarkAdapter, ParsedMessage

Dispatcher = Callable[[ParsedMessage], Awaitable[None]]


class LarkWSRuntime:
    """WebSocket transport sharing the Lark adapter and dispatcher."""

    def __init__(self, adapter: LarkAdapter, dispatcher: Dispatcher) -> None:
        self.adapter = adapter
        self.dispatcher = dispatcher
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = self._build_client()

    def _build_client(self) -> lark.ws.Client:
        domain = (
            lark.LARK_DOMAIN
            if os.environ.get("LARK_REGION", "cn").lower() == "global"
            else lark.FEISHU_DOMAIN
        )
        handler = (
            lark.EventDispatcherHandler.builder(
                encrypt_key=os.environ.get("LARK_ENCRYPT_KEY", ""),
                verification_token=os.environ.get("LARK_VERIFICATION_TOKEN", ""),
            )
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )
        return lark.ws.Client(
            os.environ["LARK_APP_ID"],
            os.environ["LARK_APP_SECRET"],
            event_handler=handler,
            domain=domain,
            log_level=lark.LogLevel.INFO,  # Keep connection state observable.
            auto_reconnect=True,
        )

    def _on_message(self, event: P2ImMessageReceiveV1) -> None:
        """Handle one callback from the lark-oapi worker thread."""
        try:
            payload = json.loads(lark.JSON.marshal(event))
        except Exception as exc:
            print(f"[lark-ws] marshal event failed: {exc}", file=sys.stderr)
            return

        parsed = self.adapter.parse(payload)
        if parsed is None:
            print(
                f"[lark-ws] parse=None; header={payload.get('header')} "
                f"message_type="
                + repr(
                    ((payload.get("event") or {}).get("message") or {}).get(
                        "message_type", "?"
                    )
                ),
                file=sys.stderr,
            )
            return

        if self._main_loop is None:
            print(
                "[lark-ws] application event loop not set; event dropped",
                file=sys.stderr,
            )
            return

        try:
            future = asyncio.run_coroutine_threadsafe(
                self.dispatcher(parsed),
                self._main_loop,
            )
            future.add_done_callback(self._report_dispatch_completion)
        except Exception as exc:
            print(f"[lark-ws] schedule dispatch failed: {exc}", file=sys.stderr)

    @staticmethod
    def _report_dispatch_completion(
        future: concurrent.futures.Future[None],
    ) -> None:
        """Make dispatcher failures observable from the WebSocket worker."""
        if future.cancelled():
            return
        try:
            future.result()
        except Exception as exc:
            print(
                f"[lark-ws] dispatch failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    def _run_client_in_thread(self) -> None:
        """Run the SDK client on a dedicated event loop.

        lark-oapi v1.6.x caches ``asyncio.get_event_loop()`` at module import
        time. Calling ``client.start()`` from a worker would otherwise invoke
        ``run_until_complete`` on uvicorn's main loop. Rebinding the SDK's
        cached loop keeps ownership within this worker thread.
        """
        thread_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(thread_loop)
        import lark_oapi.ws.client as _ws_mod

        _ws_mod.loop = thread_loop
        try:
            self._client.start()
        except Exception as exc:
            print(
                f"[lark-ws] client.start crashed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    async def start(self) -> None:
        """Run the blocking SDK client on a daemon thread."""
        self._main_loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._run_client_in_thread,
            daemon=True,
            name="lark-ws",
        )
        self._thread.start()
        print(
            "[lark-ws] starting (subscribing to im.message.receive_v1)",
            file=sys.stderr,
        )
