"""Secure loading and normalization of images sent through Lark.

This module owns the channel's file, network, data-URL, and NPY image
boundaries. It has no session or Harness state, which keeps image validation
and conversion independently testable from the ASGI application.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import binascii
import io
import ipaddress
import mimetypes
import os
import socket
import struct
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx


def _configured_image_max_bytes() -> int:
    """Return the configured upper bound for one encoded image."""
    try:
        return max(
            1,
            int(os.environ.get("LARK_IMAGE_MAX_BYTES", str(10 * 1024 * 1024))),
        )
    except ValueError:
        return 10 * 1024 * 1024


def _configured_image_max_pixels() -> int:
    """Return the configured upper bound for decoded NPY image pixels."""
    try:
        return max(
            1,
            int(os.environ.get("LARK_IMAGE_MAX_PIXELS", "20000000")),
        )
    except ValueError:
        return 20_000_000


async def _load_query_user_image(item: dict[str, Any]) -> tuple[bytes, str]:
    """Load one trusted attachment and return bytes accepted by Lark."""
    source = item["source"]
    color_order = _declared_color_order(item)
    if item.get("kind") == "path":
        path = _allowed_local_image_path(source)
        data = await asyncio.to_thread(_read_bounded_image_file, path)
        return _prepare_lark_image(path.name, data, color_order=color_order)

    parsed = urlparse(source)
    if parsed.scheme == "data":
        data, content_type = _decode_data_image_url(source)
        return _prepare_lark_image(
            source,
            data,
            content_type,
            color_order=color_order,
        )
    if parsed.scheme == "file":
        path = _allowed_local_image_path(unquote(parsed.path))
        data = await asyncio.to_thread(_read_bounded_image_file, path)
        return _prepare_lark_image(path.name, data, color_order=color_order)
    if parsed.scheme in {"http", "https"}:
        await _validate_remote_image_url(source)
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as http:
            async with http.stream("GET", source) as response:
                response.raise_for_status()
                data = await _read_bounded_remote_image(response)
                content_type = response.headers.get("content-type", "").split(
                    ";",
                    1,
                )[0]
        return _prepare_lark_image(
            source,
            data,
            content_type,
            color_order=color_order,
        )

    path = _allowed_local_image_path(source)
    data = await asyncio.to_thread(_read_bounded_image_file, path)
    return _prepare_lark_image(path.name, data, color_order=color_order)


def _read_bounded_image_file(path: Path) -> bytes:
    """Read one local image without exceeding the configured byte bound."""
    max_bytes = _configured_image_max_bytes()
    if path.stat().st_size > max_bytes:
        raise ValueError(f"Image exceeds LARK_IMAGE_MAX_BYTES={max_bytes}.")
    return path.read_bytes()


async def _read_bounded_remote_image(response: httpx.Response) -> bytes:
    """Read one streamed remote image under the configured byte bound."""
    max_bytes = _configured_image_max_bytes()
    declared = response.headers.get("content-length", "").strip()
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            declared_size = 0
        if declared_size > max_bytes:
            raise ValueError(f"Remote image exceeds LARK_IMAGE_MAX_BYTES={max_bytes}.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Remote image exceeds LARK_IMAGE_MAX_BYTES={max_bytes}.")
        chunks.append(chunk)
    return b"".join(chunks)


def _allowed_local_image_path(source: str) -> Path:
    """Resolve a local image only inside the configured channel-owned root."""
    configured_root = os.environ.get("LARK_IMAGE_ALLOWED_ROOT", "").strip()
    if not configured_root:
        raise PermissionError(
            "Local image access is disabled; set LARK_IMAGE_ALLOWED_ROOT."
        )
    root = Path(configured_root).expanduser().resolve(strict=True)
    path = Path(source).expanduser().resolve(strict=True)
    if not path.is_relative_to(root):
        raise PermissionError(f"Local image is outside LARK_IMAGE_ALLOWED_ROOT: {path}")
    if not path.is_file():
        raise PermissionError(f"Local image is not a regular file: {path}")
    return path


async def _validate_remote_image_url(source: str) -> None:
    """Reject remote-image SSRF targets before opening a network connection."""
    if os.environ.get("LARK_ALLOW_REMOTE_IMAGES", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise PermissionError(
            "Remote image fetching is disabled; set LARK_ALLOW_REMOTE_IMAGES=1."
        )

    parsed = urlparse(source)
    if parsed.scheme != "https":
        raise PermissionError("Only HTTPS remote image URLs are allowed.")
    if parsed.username or parsed.password or not parsed.hostname:
        raise PermissionError("Remote image URL has an invalid authority.")
    hostname = parsed.hostname.lower().rstrip(".")
    allowed_hosts = {
        value.strip().lower().rstrip(".")
        for value in os.environ.get("LARK_IMAGE_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    }
    if not allowed_hosts:
        raise PermissionError(
            "Remote image fetching requires LARK_IMAGE_ALLOWED_HOSTS."
        )
    if hostname not in allowed_hosts:
        raise PermissionError(
            f"Remote image host is not in LARK_IMAGE_ALLOWED_HOSTS: {hostname}"
        )

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise PermissionError(
            f"Remote image host cannot be resolved: {hostname}"
        ) from exc

    resolved = {
        ipaddress.ip_address(sockaddr[0])
        for _family, _type, _proto, _canonname, sockaddr in addresses
    }
    if not resolved or any(not address.is_global for address in resolved):
        raise PermissionError(
            "Private, loopback, link-local, reserved, and non-global "
            "remote image hosts are not allowed."
        )


def _decode_data_image_url(source: str) -> tuple[bytes, str]:
    """Decode one bounded base64 data image URL."""
    header, _, payload = source.partition(",")
    if not payload or ";base64" not in header:
        raise ValueError("unsupported data image URL")
    max_bytes = _configured_image_max_bytes()
    if len(payload) > ((max_bytes + 2) // 3) * 4 + 4:
        raise ValueError(f"Image exceeds LARK_IMAGE_MAX_BYTES={max_bytes}.")
    content_type = header.removeprefix("data:").split(";", 1)[0] or "image/png"
    try:
        data = base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("invalid base64 image data") from exc
    if len(data) > max_bytes:
        raise ValueError(f"Image exceeds LARK_IMAGE_MAX_BYTES={max_bytes}.")
    return data, content_type


def _guess_image_content_type(name: str, data: bytes) -> str:
    """Infer a supported content type from bytes, then the source name."""
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "image/png"


def _declared_color_order(item: dict[str, Any]) -> str:
    """Return an explicit BGR-family declaration, otherwise RGB semantics."""
    for metadata_key in ("color_space", "color_order"):
        value = str(item.get(metadata_key) or "").strip().upper()
        if value in {"BGR", "BGRA"}:
            return value
    return ""


def _prepare_lark_image(
    name: str,
    data: bytes,
    content_type: str = "",
    *,
    color_order: str = "",
) -> tuple[bytes, str]:
    """Return real raster bytes suitable for the Lark image upload API."""
    max_bytes = _configured_image_max_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"Image exceeds LARK_IMAGE_MAX_BYTES={max_bytes}.")
    if data.startswith(b"\x93NUMPY"):
        return (
            _npy_image_bytes_to_png(data, color_order=color_order),
            "image/png",
        )
    return data, content_type or _guess_image_content_type(name, data)


def _npy_image_bytes_to_png(data: bytes, *, color_order: str = "") -> bytes:
    """Convert a uint8 NPY image to PNG, honoring explicit channel metadata."""
    stream = io.BytesIO(data)
    header = _read_npy_image_header(stream)
    height, width, channels, color_type = _npy_image_layout(header)
    row_size = width * channels
    pixels_for_png = _read_npy_pixels(
        stream,
        height * row_size,
        channels,
        color_order=color_order,
    )
    filtered = _png_scanlines(pixels_for_png, height, row_size)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(filtered))
        + _png_chunk(b"IEND", b"")
    )


def _read_npy_image_header(stream: io.BytesIO) -> dict[str, Any]:
    """Parse and validate one NPY header."""
    if stream.read(6) != b"\x93NUMPY":
        raise ValueError("invalid npy image")
    major, minor = stream.read(2)
    header_len, encoding = _npy_header_format(stream, major, minor)
    try:
        header = ast.literal_eval(stream.read(header_len).decode(encoding).strip())
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid npy header: {exc}") from exc
    if not isinstance(header, dict):
        raise ValueError("invalid npy header")
    return header


def _npy_header_format(
    stream: io.BytesIO,
    major: int,
    minor: int,
) -> tuple[int, str]:
    """Return the header length and encoding for a supported NPY version."""
    if (major, minor) == (1, 0):
        header_len = struct.unpack("<H", stream.read(2))[0]
        encoding = "latin1"
    elif (major, minor) in {(2, 0), (3, 0)}:
        header_len = struct.unpack("<I", stream.read(4))[0]
        encoding = "utf-8" if major == 3 else "latin1"
    else:
        raise ValueError(f"unsupported npy version: {major}.{minor}")
    return header_len, encoding


def _npy_image_layout(header: dict[str, Any]) -> tuple[int, int, int, int]:
    """Validate NPY layout and return PNG dimensions and color type."""
    if header.get("fortran_order"):
        raise ValueError("fortran-order npy images are not supported")
    if header.get("descr") not in {"|u1", "<u1", ">u1"}:
        raise ValueError(f"unsupported npy dtype: {header.get('descr')}")

    shape = header.get("shape")
    if not isinstance(shape, tuple) or len(shape) not in {2, 3}:
        raise ValueError(f"unsupported npy image shape: {shape}")
    if not all(isinstance(dim, int) and dim > 0 for dim in shape):
        raise ValueError(f"invalid npy image shape: {shape}")

    height, width = shape[:2]
    channels = shape[2] if len(shape) == 3 else 1
    max_pixels = _configured_image_max_pixels()
    if height * width > max_pixels:
        raise ValueError(f"NPY image exceeds LARK_IMAGE_MAX_PIXELS={max_pixels}.")
    color_type = {1: 0, 3: 2, 4: 6}.get(channels)
    if color_type is None:
        raise ValueError(f"unsupported npy channel count: {channels}")
    return height, width, channels, color_type


def _read_npy_pixels(
    stream: io.BytesIO,
    expected: int,
    channels: int,
    *,
    color_order: str = "",
) -> bytes:
    """Read one NPY payload and normalize explicitly declared BGR channels."""
    pixels = stream.read()
    if len(pixels) < expected:
        raise ValueError("truncated npy image data")

    pixels_for_png = bytearray(pixels[:expected])
    if channels in {3, 4} and color_order.strip().upper() in {"BGR", "BGRA"}:
        for offset in range(0, expected, channels):
            pixels_for_png[offset], pixels_for_png[offset + 2] = (
                pixels_for_png[offset + 2],
                pixels_for_png[offset],
            )
    return bytes(pixels_for_png)


def _png_scanlines(pixels: bytes, height: int, row_size: int) -> bytes:
    """Prefix raw PNG rows with the no-filter marker."""
    filtered = bytearray()
    for row in range(height):
        start = row * row_size
        filtered.append(0)
        filtered.extend(pixels[start : start + row_size])
    return bytes(filtered)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk with its CRC."""
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _query_user_image_filename(source: str, index: int, content_type: str) -> str:
    """Choose a safe filename for one uploaded Lark image."""
    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(content_type, ".png")
    parsed_name = Path(urlparse(source).path).name
    if parsed_name and Path(parsed_name).suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }:
        return parsed_name
    if parsed_name:
        return f"{Path(parsed_name).stem}{suffix}"
    return f"query_user_{index}{suffix}"


__all__ = []
