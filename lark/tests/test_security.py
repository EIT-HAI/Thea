from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import time
import zlib
from pathlib import Path

import pytest
from thea_lark import channel_delivery as delivery_module
from thea_lark import server
from thea_lark.adapter import LarkAdapter
from thea_lark.image_transport import (
    _load_query_user_image,
    _npy_image_bytes_to_png,
)


def _text_event(open_id: str = "ou_allowed") -> dict:
    return {
        "header": {"event_id": f"event-{open_id}"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}},
            "message": {
                "chat_id": "oc_chat",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "hello"}),
            },
        },
    }


def test_webhook_auth_fails_closed_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LARK_ENCRYPT_KEY", raising=False)
    monkeypatch.delenv("LARK_VERIFICATION_TOKEN", raising=False)
    adapter = LarkAdapter()

    assert not adapter.webhook_auth_configured
    assert not adapter.verify({}, b"{}")
    assert not adapter.verify_payload_token({})


def test_webhook_endpoint_rejects_missing_auth_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LARK_ENCRYPT_KEY", raising=False)
    monkeypatch.delenv("LARK_VERIFICATION_TOKEN", raising=False)
    monkeypatch.setattr(server.state, "adapter", LarkAdapter())

    class Request:
        headers: dict[str, str] = {}

        async def body(self) -> bytes:
            return b"{}"

    response = asyncio.run(server.webhook(Request()))

    assert response.status_code == 503


def test_webhook_signature_requires_all_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_ENCRYPT_KEY", "secret")
    monkeypatch.delenv("LARK_VERIFICATION_TOKEN", raising=False)
    adapter = LarkAdapter()
    body = b'{"event":"test"}'
    timestamp = str(int(time.time()))
    nonce = "abc"
    signature = hashlib.sha256(
        (timestamp + nonce + "secret").encode() + body
    ).hexdigest()

    assert not adapter.verify({}, body)
    assert adapter.verify(
        {
            "X-Lark-Signature": signature,
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": nonce,
        },
        body,
    )


def test_webhook_signature_rejects_stale_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_ENCRYPT_KEY", "secret")
    monkeypatch.setenv("LARK_WEBHOOK_MAX_SKEW_SEC", "300")
    adapter = LarkAdapter()
    body = b'{"event":"test"}'
    timestamp = str(int(time.time()) - 301)
    nonce = "abc"
    signature = hashlib.sha256(
        (timestamp + nonce + "secret").encode() + body
    ).hexdigest()

    assert not adapter.verify(
        {
            "X-Lark-Signature": signature,
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": nonce,
        },
        body,
    )


def test_webhook_token_must_be_present_and_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LARK_ENCRYPT_KEY", raising=False)
    monkeypatch.setenv("LARK_VERIFICATION_TOKEN", "expected")
    adapter = LarkAdapter()

    assert adapter.verify({}, b"{}")
    assert not adapter.verify_payload_token({})
    assert not adapter.verify_payload_token({"token": "wrong"})
    assert adapter.verify_payload_token({"header": {"token": "expected"}})


def test_user_authorization_fails_closed_and_accepts_configured_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LARK_ALLOWED_OPEN_IDS", raising=False)
    assert LarkAdapter().parse(_text_event()) is None

    monkeypatch.setenv("LARK_ALLOWED_OPEN_IDS", "ou_allowed, ou_other")
    parsed = LarkAdapter().parse(_text_event())
    assert parsed is not None
    assert parsed.user_id == "ou_allowed"
    assert parsed.chat_id == "oc_chat"
    assert LarkAdapter().parse(_text_event("ou_denied")) is None


def test_user_authorization_wildcard_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_ALLOWED_OPEN_IDS", "*")

    parsed = LarkAdapter().parse(_text_event("ou_any"))

    assert parsed is not None
    assert parsed.user_id == "ou_any"


def test_local_image_paths_require_an_allowed_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.delenv("LARK_IMAGE_ALLOWED_ROOT", raising=False)

    with pytest.raises(PermissionError, match="disabled"):
        asyncio.run(
            _load_query_user_image(
                {"kind": "path", "source": str(image), "caption": ""}
            )
        )

    monkeypatch.setenv("LARK_IMAGE_ALLOWED_ROOT", str(tmp_path))
    data, content_type = asyncio.run(
        _load_query_user_image({"kind": "path", "source": str(image), "caption": ""})
    )
    assert data == b"\x89PNG\r\n\x1a\n"
    assert content_type == "image/png"


def test_local_image_path_cannot_escape_allowed_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("LARK_IMAGE_ALLOWED_ROOT", str(allowed))

    with pytest.raises(PermissionError, match="outside"):
        asyncio.run(
            _load_query_user_image(
                {"kind": "path", "source": str(outside), "caption": ""}
            )
        )


def test_image_delivery_failure_does_not_disclose_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_path = tmp_path / "private" / "camera.png"
    sent: list[str] = []

    class Client:
        async def send_text(
            self,
            _receive_id: str,
            text: str,
            **_kwargs,
        ) -> dict:
            sent.append(text)
            return {"data": {"message_id": "om_failure"}}

    async def fail_to_load(_item: dict) -> tuple[bytes, str]:
        raise FileNotFoundError(str(secret_path))

    monkeypatch.setattr(server.state, "client", lambda: Client())
    monkeypatch.setattr(
        delivery_module,
        "_load_query_user_image",
        fail_to_load,
    )

    asyncio.run(
        server.delivery.send_image_attachments(
            "ou_user",
            [
                {
                    "kind": "path",
                    "source": str(secret_path),
                    "caption": "current view",
                }
            ],
            context="test",
        )
    )

    assert sent == [
        "Image 1 (current view) upload failed. Capture a fresh image and try again."
    ]
    assert str(secret_path) not in sent[0]


def test_remote_images_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LARK_ALLOW_REMOTE_IMAGES", raising=False)

    with pytest.raises(PermissionError, match="disabled"):
        asyncio.run(
            _load_query_user_image(
                {
                    "kind": "url",
                    "source": "https://example.com/image.png",
                    "caption": "",
                }
            )
        )


def test_loopback_remote_image_is_rejected_when_fetching_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_ALLOW_REMOTE_IMAGES", "1")
    monkeypatch.setenv("LARK_IMAGE_ALLOWED_HOSTS", "127.0.0.1")

    with pytest.raises(PermissionError, match="non-global"):
        asyncio.run(
            _load_query_user_image(
                {
                    "kind": "url",
                    "source": "https://127.0.0.1/image.png",
                    "caption": "",
                }
            )
        )


def test_remote_image_host_requires_an_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LARK_ALLOW_REMOTE_IMAGES", "1")
    monkeypatch.setenv("LARK_IMAGE_ALLOWED_HOSTS", "images.example.org")

    with pytest.raises(PermissionError, match="not in LARK_IMAGE_ALLOWED_HOSTS"):
        asyncio.run(
            _load_query_user_image(
                {
                    "kind": "url",
                    "source": "https://example.com/image.png",
                    "caption": "",
                }
            )
        )


def _npy_image(
    version: tuple[int, int],
    shape: tuple[int, ...],
    pixels: bytes,
) -> bytes:
    header = repr(
        {
            "descr": "|u1",
            "fortran_order": False,
            "shape": shape,
        }
    ).encode("latin1")
    header_len = (
        struct.pack("<H", len(header))
        if version == (1, 0)
        else struct.pack("<I", len(header))
    )
    return b"\x93NUMPY" + bytes(version) + header_len + header + pixels


def _decompress_png_scanlines(png: bytes) -> bytes:
    offset = 8
    idat = bytearray()
    while offset < len(png):
        chunk_len = struct.unpack(">I", png[offset : offset + 4])[0]
        chunk_type = png[offset + 4 : offset + 8]
        chunk_data = png[offset + 8 : offset + 8 + chunk_len]
        if chunk_type == b"IDAT":
            idat.extend(chunk_data)
        offset += chunk_len + 12
    return zlib.decompress(bytes(idat))


@pytest.mark.parametrize(
    ("version", "shape", "pixels", "expected_scanlines"),
    [
        ((1, 0), (1, 2), b"\x01\x02", b"\x00\x01\x02"),
        ((2, 0), (1, 1, 3), b"\x01\x02\x03", b"\x00\x01\x02\x03"),
        ((3, 0), (1, 1, 4), b"\x01\x02\x03\x04", b"\x00\x01\x02\x03\x04"),
    ],
)
def test_npy_image_conversion_defaults_to_rgb_and_rgba(
    version: tuple[int, int],
    shape: tuple[int, ...],
    pixels: bytes,
    expected_scanlines: bytes,
) -> None:
    png = _npy_image_bytes_to_png(_npy_image(version, shape, pixels))

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert _decompress_png_scanlines(png) == expected_scanlines


@pytest.mark.parametrize(
    ("shape", "metadata_field", "color_order", "expected_scanlines"),
    [
        ((1, 1, 3), "color_space", "BGR", b"\x00\x03\x02\x01"),
        ((1, 1, 4), "color_order", "BGRA", b"\x00\x03\x02\x01\x04"),
    ],
)
def test_npy_image_conversion_swaps_only_explicit_bgr_visual_outputs(
    shape: tuple[int, ...],
    metadata_field: str,
    color_order: str,
    expected_scanlines: bytes,
) -> None:
    data = _npy_image((1, 0), shape, b"\x01\x02\x03\x04"[: shape[-1]])

    attachments = server.delivery.collect_query_user_visuals(
        visual_outputs={
            "path": "/tmp/image.npy",
            metadata_field: color_order,
        }
    )
    png = _npy_image_bytes_to_png(
        data,
        color_order=attachments[0][metadata_field],
    )

    assert attachments[0][metadata_field] == color_order
    assert _decompress_png_scanlines(png) == expected_scanlines


def test_npy_image_conversion_rejects_truncated_pixels() -> None:
    data = _npy_image((1, 0), (1, 1, 3), b"\x01\x02")

    with pytest.raises(ValueError, match="truncated npy image data"):
        _npy_image_bytes_to_png(data)
