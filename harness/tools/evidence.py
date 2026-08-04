"""Transient visual payloads associated with an accumulated Tool Result."""

from __future__ import annotations

import base64
from typing import Any

from harness.context import ToolCall

IMAGE_OMITTED = "<image payload omitted; see visual_outputs>"
BINARY_OMITTED = "<binary payload omitted>"


def detect_image_media_type(data: bytes) -> str:
    """Infer a supported image media type from common file signatures."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def build_user_content(text: str, images: list[bytes] | None) -> Any:
    """Build provider-neutral text and image blocks for one instruction."""
    if not images:
        return text
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for image in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": detect_image_media_type(image),
                    "data": base64.b64encode(image).decode("ascii"),
                },
            }
        )
    return blocks


def strip_image_payloads(value: Any) -> Any:
    """Replace inline image payloads while retaining Tool Result structure."""
    if isinstance(value, dict):
        is_base64_image = value.get("type") == "base64" and str(
            value.get("media_type") or ""
        ).startswith("image/")
        return {
            key: (
                IMAGE_OMITTED
                if is_base64_image and key == "data"
                else strip_image_payloads(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [strip_image_payloads(item) for item in value]
    if isinstance(value, bytes):
        return BINARY_OMITTED
    if isinstance(value, str) and value.startswith("data:image/"):
        return IMAGE_OMITTED
    return value


def tool_result_for_model(result: dict[str, Any]) -> dict[str, Any]:
    """Return the accumulated Tool Result without inline image payloads."""
    sanitized = strip_image_payloads(result)
    return sanitized if isinstance(sanitized, dict) else dict(result)


def transient_tool_result_evidence_message(
    call: ToolCall,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Render a Tool Result's visual payload for the next decision only.

    The compact textual Tool Result remains in Accumulated context. The image
    payload is a transient provider-message transport detail, not a fourth
    context lifetime, and disappears after the next model call.
    """
    if not bool(result.get("success")):
        return None
    outputs = result.get("visual_outputs")
    outputs = outputs if isinstance(outputs, list) else []
    images = [
        item
        for item in outputs
        if isinstance(item, dict) and item.get("type") in {"image", "image_url"}
    ]
    if not images:
        return None
    ref = str(
        result.get("ref")
        or call.arguments.get("obj")
        or call.arguments.get("ref")
        or ""
    ).strip()
    subject = f" for ref {ref}" if ref else ""
    evidence_text = (
        f"Visual evidence returned by {call.name}{subject}. "
        "Use it for this decision only."
    )
    provenance = str(result.get("provenance") or "").strip().lower()
    if call.name == "get_image" or provenance == "scene_graph":
        evidence_text += (
            " Stored Scene Graph evidence may be historical, so check its "
            "freshness metadata."
        )
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": evidence_text,
            },
            *images,
        ],
        "kind": "transient_tool_result_evidence",
        "source": call.name,
    }


__all__ = [
    "BINARY_OMITTED",
    "IMAGE_OMITTED",
    "build_user_content",
    "detect_image_media_type",
    "strip_image_payloads",
    "tool_result_for_model",
    "transient_tool_result_evidence_message",
]
