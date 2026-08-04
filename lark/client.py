"""Lark Open Platform HTTP client.

The client obtains and caches the tenant access token for its roughly two-hour
lifetime. All outbound messages and cards pass through this boundary.
``LARK_REGION`` selects the China or global API domain.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx

LARK_API_CN = "https://open.feishu.cn/open-apis"
LARK_API_GLOBAL = "https://open.larksuite.com/open-apis"
TOKEN_TTL_BUFFER = 300  # Refresh five minutes before expiry.
DEFAULT_HTTP_TIMEOUT = 15.0
DEFAULT_HTTP_RETRIES = 3
DEFAULT_CONNECT_RETRIES = 2
RETRYABLE_HTTP_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


class LarkClient:
    """Async client for authenticated Lark messages, cards, and image assets."""

    def __init__(self) -> None:
        self.app_id = os.environ["LARK_APP_ID"]
        self.app_secret = os.environ["LARK_APP_SECRET"]
        self.api_base = (
            LARK_API_GLOBAL
            if os.environ.get("LARK_REGION", "cn").lower() == "global"
            else LARK_API_CN
        )
        self._token: str = ""
        self._token_exp: float = 0.0
        self._lock = asyncio.Lock()
        self._request_retries = _env_int("LARK_HTTP_RETRIES", DEFAULT_HTTP_RETRIES)
        proxy = _lark_http_proxy()
        self._http = httpx.AsyncClient(
            timeout=_env_float("LARK_HTTP_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT),
            transport=httpx.AsyncHTTPTransport(
                proxy=proxy,
                retries=_env_int("LARK_HTTP_CONNECT_RETRIES", DEFAULT_CONNECT_RETRIES),
            ),
            trust_env=False,
        )

    # ============================================================
    # Token
    # ============================================================

    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._token_exp:
                return self._token
            r = await self._request(
                "POST",
                f"{self.api_base}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(f"lark token refresh failed: {data}")
            self._token = data["tenant_access_token"]
            self._token_exp = time.time() + int(data["expire"]) - TOKEN_TTL_BUFFER
            return self._token

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"{self.api_base}{path}",
            params=params or {},
            headers={
                "Authorization": f"Bearer {await self._ensure_token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (None, 0):
            raise RuntimeError(f"lark api {path} failed: {data}")
        return data

    # ============================================================
    # Outbound text
    # ============================================================

    async def send_text(
        self,
        receive_id: str,
        text: str,
        *,
        receive_id_type: str = "open_id",
    ) -> dict[str, Any]:
        """POST /im/v1/messages — receive_id_type ∈ {open_id, chat_id, user_id}."""
        return await self._post(
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            body={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        """Reply to the originating message thread."""
        return await self._post(
            f"/im/v1/messages/{message_id}/reply",
            body={
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )

    # ============================================================
    # Outbound image
    # ============================================================

    async def upload_image_bytes(
        self,
        image: bytes,
        *,
        filename: str = "image.png",
        content_type: str = "image/png",
        image_type: str = "message",
    ) -> str:
        """Upload image bytes and return Lark image_key for message sending."""
        r = await self._request(
            "POST",
            f"{self.api_base}/im/v1/images",
            headers={"Authorization": f"Bearer {await self._ensure_token()}"},
            data={"image_type": image_type},
            files={"image": (filename, image, content_type)},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"lark image upload failed: {data}")
        return (data.get("data") or {})["image_key"]

    async def send_image(
        self,
        receive_id: str,
        image_key: str,
        *,
        receive_id_type: str = "open_id",
    ) -> dict[str, Any]:
        """POST /im/v1/messages with msg_type=image."""
        return await self._post(
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            body={
                "receive_id": receive_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
            },
        )

    async def send_image_bytes(
        self,
        receive_id: str,
        image: bytes,
        *,
        filename: str = "image.png",
        content_type: str = "image/png",
        receive_id_type: str = "open_id",
    ) -> dict[str, Any]:
        """Upload image bytes, then send them as a Lark image message."""
        image_key = await self.upload_image_bytes(
            image,
            filename=filename,
            content_type=content_type,
        )
        return await self.send_image(
            receive_id,
            image_key,
            receive_id_type=receive_id_type,
        )

    # ============================================================
    # Interactive card (streaming UX)
    # ============================================================

    async def send_card(
        self,
        receive_id: str,
        card: dict[str, Any],
        *,
        receive_id_type: str = "open_id",
    ) -> dict[str, Any]:
        """Send an interactive card and return its patchable message ID."""
        return await self._post(
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            body={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )

    async def reply_card(self, message_id: str, card: dict[str, Any]) -> dict[str, Any]:
        """Reply to a message thread with an interactive card."""
        return await self._post(
            f"/im/v1/messages/{message_id}/reply",
            body={
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )

    async def patch_card(self, message_id: str, card: dict[str, Any]) -> dict[str, Any]:
        """Patch an interactive card; Lark does not support patching text."""
        r = await self._request(
            "PATCH",
            f"{self.api_base}/im/v1/messages/{message_id}",
            headers={
                "Authorization": f"Bearer {await self._ensure_token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={"content": json.dumps(card, ensure_ascii=False)},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") not in (None, 0):
            raise RuntimeError(f"lark patch_card failed: {data}")
        return data

    # ============================================================
    # Resource download (Lark image/file fetching)
    # ============================================================

    async def download_resource(
        self,
        message_id: str,
        file_key: str,
        *,
        file_type: str = "image",
    ) -> bytes:
        """Download the original image or file bytes for a message resource.

        ``file_type`` is either ``image`` or ``file``.
        """
        r = await self._request(
            "GET",
            f"{self.api_base}/im/v1/messages/{message_id}/resources/{file_key}",
            params={"type": file_type},
            headers={"Authorization": f"Bearer {await self._ensure_token()}"},
        )
        r.raise_for_status()
        return r.content

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(max(1, self._request_retries)):
            try:
                return await self._http.request(method, url, **kwargs)
            except RETRYABLE_HTTP_ERRORS as exc:
                last_exc = exc
                if attempt >= self._request_retries - 1:
                    break
                await asyncio.sleep(0.4 * (attempt + 1))
        assert last_exc is not None
        raise last_exc


def _lark_http_proxy() -> str | None:
    explicit = os.environ.get("LARK_HTTP_PROXY", "").strip()
    if explicit:
        return explicit
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default
