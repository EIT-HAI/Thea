"""Lark inbound webhook handling.

Inbound contract, in server call order:
    1. verify(headers, body)             - authenticate the signature
    2. handle_url_verification(payload)  - answer the URL handshake
    3. parse(payload) -> ParsedMessage   - deduplicate and normalize content

``parse`` returns ``None`` when an event should be silently ignored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


@dataclass
class ParsedMessage:
    """Authorized Lark message normalized for one Harness task."""

    user_id: str  # Sender open_id.
    text: str  # Normalized text with the bot mention removed.
    raw: dict  # Original payload used to resolve reply identifiers.
    image_keys: list[str] = field(default_factory=list)  # Downloadable Lark keys.
    chat_id: str = ""  # Conversation that delivered the message.


class LarkAdapter:
    """Authenticate, deduplicate, decrypt, and normalize inbound Lark events."""

    _DEDUP_TTL_SEC = 60
    _DEDUP_MAX = 1024
    _DEFAULT_WEBHOOK_MAX_SKEW_SEC = 300

    def __init__(self) -> None:
        self.encrypt_key = os.environ.get("LARK_ENCRYPT_KEY", "")
        self.verification_token = os.environ.get("LARK_VERIFICATION_TOKEN", "")
        self.bot_open_id = os.environ.get("LARK_BOT_OPEN_ID", "")
        self.allowed_open_ids = frozenset(
            value.strip()
            for value in os.environ.get("LARK_ALLOWED_OPEN_IDS", "").split(",")
            if value.strip()
        )
        self._seen: OrderedDict[str, float] = OrderedDict()

    def is_user_authorized(self, open_id: str) -> bool:
        """Authorize an open_id, failing closed unless ``*`` is explicit."""
        return bool(open_id) and (
            "*" in self.allowed_open_ids or open_id in self.allowed_open_ids
        )

    # ============================================================
    # Signature (Lark v2: sha256(ts + nonce + encrypt_key + body))
    # ============================================================

    @property
    def webhook_auth_configured(self) -> bool:
        """Return whether webhook events have a configured trust anchor."""
        return bool(self.encrypt_key or self.verification_token)

    def verify(self, headers: dict, body: bytes) -> bool:
        """Verify X-Lark-Signature.

        With an Encrypt Key, all signature fields are mandatory. Without one,
        the payload verification token is checked separately after decoding.
        No configured trust anchor fails closed.
        """
        if not self.encrypt_key:
            return bool(self.verification_token)
        sig = headers.get("X-Lark-Signature") or headers.get("x-lark-signature", "")
        ts = headers.get("X-Lark-Request-Timestamp") or headers.get(
            "x-lark-request-timestamp", ""
        )
        nonce = headers.get("X-Lark-Request-Nonce") or headers.get(
            "x-lark-request-nonce", ""
        )
        if not (sig and ts and nonce):
            return False
        if not self._timestamp_is_fresh(ts):
            return False
        digest = hashlib.sha256(
            (ts + nonce + self.encrypt_key).encode() + body
        ).hexdigest()
        return hmac.compare_digest(sig, digest)

    def _timestamp_is_fresh(self, value: str) -> bool:
        try:
            timestamp = float(value)
            max_skew = max(
                1.0,
                float(
                    os.environ.get(
                        "LARK_WEBHOOK_MAX_SKEW_SEC",
                        str(self._DEFAULT_WEBHOOK_MAX_SKEW_SEC),
                    )
                ),
            )
        except (TypeError, ValueError):
            return False
        return abs(time.time() - timestamp) <= max_skew

    def verify_payload_token(self, payload: dict) -> bool:
        """Validate the webhook token when one is configured."""
        if not self.verification_token:
            return bool(self.encrypt_key)
        got = (payload.get("header") or {}).get("token") or payload.get("token", "")
        return bool(got) and hmac.compare_digest(
            str(got),
            self.verification_token,
        )

    # ============================================================
    # AES v2 decryption for encrypted webhook bodies.
    # ============================================================

    def unwrap_encrypted(self, encrypted_b64: str) -> dict:
        """Lark AES v2: key=sha256(encrypt_key), AES-256-CBC, IV=first 16 bytes, PKCS7."""
        if not self.encrypt_key:
            raise RuntimeError("Received an encrypted body without LARK_ENCRYPT_KEY.")
        key = hashlib.sha256(self.encrypt_key.encode()).digest()
        raw = base64.b64decode(encrypted_b64)
        iv, ciphertext = raw[:16], raw[16:]
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = decryptor.update(ciphertext) + decryptor.finalize()
        if not plain:
            raise ValueError("Encrypted body is empty after decryption.")
        pad = plain[-1]
        if pad < 1 or pad > 16 or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError("Encrypted body has invalid PKCS7 padding.")
        return json.loads(plain[:-pad].decode("utf-8"))

    # ============================================================
    # URL handshake sent when an event endpoint is registered.
    # ============================================================

    def handle_url_verification(self, payload: dict) -> dict | None:
        if payload.get("type") == "url_verification" and "challenge" in payload:
            return {"challenge": payload["challenge"]}
        return None

    # ============================================================
    # Deduplicate event retries after Lark's delivery timeout.
    # ============================================================

    def _is_duplicate(self, event_id: str) -> bool:
        now = time.time()
        # evict expired
        while (
            self._seen and now - next(iter(self._seen.values())) > self._DEDUP_TTL_SEC
        ):
            self._seen.popitem(last=False)
        if event_id in self._seen:
            return True
        self._seen[event_id] = now
        if len(self._seen) > self._DEDUP_MAX:
            self._seen.popitem(last=False)
        return False

    # ============================================================
    # Parse + filter
    # ============================================================

    @staticmethod
    def _parse_post_content(content_obj: dict) -> tuple[str, list[str]]:
        """Flatten supported rich-text segments into text and image keys."""
        parts: list[str] = []
        image_keys: list[str] = []
        if title := (content_obj.get("title") or "").strip():
            parts.append(title)
        for line in content_obj.get("content") or []:
            for segment in line or []:
                tag = segment.get("tag", "")
                if tag == "text" and segment.get("text"):
                    parts.append(segment["text"])
                elif tag == "a" and segment.get("text"):
                    parts.append(segment["text"])
                elif tag == "img" and segment.get("image_key"):
                    image_keys.append(segment["image_key"])
                # Ignore mentions, emoji, and unsupported segment types.
        return " ".join(parts).strip(), image_keys

    @classmethod
    def _parse_message_content(
        cls,
        message_type: str,
        content_obj: dict,
    ) -> tuple[str, list[str]] | None:
        """Normalize the supported Lark message content types."""
        if message_type == "text":
            return (content_obj.get("text") or "").strip(), []
        if message_type == "image":
            image_key = content_obj.get("image_key", "")
            return "", [image_key] if image_key else []
        if message_type == "post":
            return cls._parse_post_content(content_obj)
        return None

    def parse(self, payload: dict) -> ParsedMessage | None:
        # 1) verification_token guard (header.token v2 / token v1).
        # Webhooks carry a token. WebSocket events are already authenticated by
        # the App ID and secret and may omit it, so only reject a present token
        # that does not match.
        got = (payload.get("header") or {}).get("token") or payload.get("token", "")
        if got and self.verification_token and got != self.verification_token:
            return None

        # 2) dedup by event_id
        event_id = (payload.get("header") or {}).get("event_id") or payload.get(
            "uuid", ""
        )
        if event_id and self._is_duplicate(event_id):
            return None

        # 3) extract user_id + text
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        user_id = (sender.get("sender_id") or {}).get("open_id", "")
        if not self.is_user_authorized(user_id):
            return None

        msg_type = message.get("message_type", "")
        try:
            content_obj = json.loads(message.get("content") or "{}")
        except (json.JSONDecodeError, TypeError):
            return None

        parsed_content = self._parse_message_content(msg_type, content_obj)
        if parsed_content is None:
            return None  # Audio, video, stickers, and files are unsupported.
        text, image_keys = parsed_content

        if not text and not image_keys:
            return None

        # 4) mention strip + group gate
        mentions = message.get("mentions") or []
        bot_mention_keys = [
            m.get("key", "")
            for m in mentions
            if (m.get("id") or {}).get("open_id") == self.bot_open_id
        ]
        for key in bot_mention_keys:
            text = text.replace(key, "").strip()

        if message.get("chat_type") == "group" and not bot_mention_keys:
            return None  # Ignore group messages that do not mention the bot.

        return ParsedMessage(
            user_id=user_id,
            text=text,
            raw=payload,
            chat_id=str(message.get("chat_id") or ""),
            image_keys=image_keys,
        )
