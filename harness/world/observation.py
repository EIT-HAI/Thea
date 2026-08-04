"""Provider-neutral records and rendering for the current Observation.

An Observation is refreshed for each model decision.  This module deliberately
does not capture sensor data itself: deployments construct the typed records
below, then publish them through :class:`harness.context.ObservationSink`.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field
from typing import Any

from harness.context import Message, ObservationSink
from harness.tools.evidence import detect_image_media_type

DEFAULT_MAX_OBSERVATION_VISUALS = 8
DEFAULT_MAX_OBSERVATION_SUMMARY_CHARS = 500
_MAX_NAME_CHARS = 128
_MAX_CAPTION_CHARS = 240


@dataclass(frozen=True, slots=True)
class VisualEvidence:
    """One named image available for the current model decision.

    ``name`` is a stable deployment-facing view name such as ``"front"`` or
    ``"wrist"``.  It is intentionally not tied to a camera model or robot.
    Exactly one of ``url`` and ``data`` must be supplied.  Local deployment
    paths are not part of this public boundary.
    """

    name: str
    url: str = ""
    data: bytes | None = None
    media_type: str = ""
    caption: str = ""
    freshness: str | int | float | None = None

    def __post_init__(self) -> None:
        name = _bounded_text(self.name, field_name="name", limit=_MAX_NAME_CHARS)
        caption = _bounded_text(
            self.caption,
            field_name="caption",
            limit=_MAX_CAPTION_CHARS,
            allow_empty=True,
        )
        url = str(self.url or "").strip()
        has_url = bool(url)
        has_data = self.data is not None
        if has_url == has_data:
            raise ValueError("VisualEvidence requires exactly one of url or data")
        if has_data and not isinstance(self.data, bytes):
            raise TypeError("VisualEvidence.data must be bytes")
        if has_data and not self.data:
            raise ValueError("VisualEvidence.data must not be empty")

        media_type = str(self.media_type or "").strip().lower()
        if media_type and not media_type.startswith("image/"):
            raise ValueError("VisualEvidence.media_type must be an image media type")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "caption", caption)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "media_type", media_type)

    @classmethod
    def from_bytes(
        cls,
        name: str,
        data: bytes,
        *,
        media_type: str = "",
        caption: str = "",
        freshness: str | int | float | None = None,
    ) -> VisualEvidence:
        """Build named visual evidence from encoded image bytes."""
        return cls(
            name=name,
            data=data,
            media_type=media_type,
            caption=caption,
            freshness=freshness,
        )

    def image_url(self) -> str:
        """Return an HTTP(S) or data URL accepted by provider adapters."""
        if self.url:
            return self.url
        assert self.data is not None
        media_type = self.media_type or detect_image_media_type(self.data)
        payload = base64.b64encode(self.data).decode("ascii")
        return f"data:{media_type};base64,{payload}"

    def content_block(self) -> dict[str, Any]:
        """Render one provider-neutral multimodal image block."""
        block: dict[str, Any] = {
            "type": "image_url",
            "image_url": {"url": self.image_url()},
            "caption": self.caption or self.name,
        }
        return block

    def visual_output(self) -> dict[str, Any]:
        """Render the public visual output consumed by channel adapters."""
        output = self.content_block()
        output.update(
            {
                "view": self.name,
                "label": self.name,
            }
        )
        if self.freshness not in (None, ""):
            output["freshness"] = self.freshness
        return output


@dataclass(frozen=True, slots=True)
class BaseClearance:
    """Nearest obstacle clearance around the base, measured in metres."""

    forward_m: float
    backward_m: float
    left_m: float
    right_m: float

    def __post_init__(self) -> None:
        for field_name in (
            "forward_m",
            "backward_m",
            "left_m",
            "right_m",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"BaseClearance.{field_name} must be a number")
            normalized = float(value)
            if not math.isfinite(normalized) or normalized < 0:
                raise ValueError(
                    f"BaseClearance.{field_name} must be finite and non-negative"
                )
            object.__setattr__(self, field_name, normalized)

    def as_dict(self) -> dict[str, float]:
        """Return the four named measurements in the paper's order."""
        return {
            "forward_m": self.forward_m,
            "backward_m": self.backward_m,
            "left_m": self.left_m,
            "right_m": self.right_m,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    """Current visual and base-clearance evidence for one model decision."""

    visuals: tuple[VisualEvidence, ...] = field(default_factory=tuple)
    base_clearance: BaseClearance | None = None
    captured_at: str | int | float | None = None
    provenance: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        visuals = tuple(self.visuals)
        if any(not isinstance(item, VisualEvidence) for item in visuals):
            raise TypeError("Observation.visuals must contain VisualEvidence records")
        names = [item.name for item in visuals]
        if len(set(names)) != len(names):
            raise ValueError("Observation visual names must be unique")
        if self.base_clearance is not None and not isinstance(
            self.base_clearance,
            BaseClearance,
        ):
            raise TypeError("Observation.base_clearance must be BaseClearance")
        object.__setattr__(self, "visuals", visuals)
        object.__setattr__(self, "provenance", str(self.provenance or "").strip())
        object.__setattr__(self, "summary", str(self.summary or "").strip())


def render_observation_messages(
    observation: Observation,
    *,
    max_visuals: int = DEFAULT_MAX_OBSERVATION_VISUALS,
) -> list[Message]:
    """Render the current Observation as one provider-neutral user message.

    The returned message belongs in ``ObservationSink`` only.  It is not an
    accumulated user message and is expected to be replaced before the next
    model decision.
    """
    selected_visuals = _selected_visuals(observation, max_visuals=max_visuals)
    if not selected_visuals and observation.base_clearance is None:
        return []

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _render_observation_text(
                observation,
                selected_visual_count=len(selected_visuals),
            ),
        }
    ]
    content.extend(item.content_block() for item in selected_visuals)
    return [
        {
            "role": "user",
            "content": content,
            "kind": "observation",
            "source": "current_observation",
        }
    ]


def publish_observation(
    sink: ObservationSink,
    observation: Observation,
    *,
    turn: int,
    max_visuals: int = DEFAULT_MAX_OBSERVATION_VISUALS,
    max_summary_chars: int = DEFAULT_MAX_OBSERVATION_SUMMARY_CHARS,
) -> dict[str, Any]:
    """Replace the current Observation and return a bounded public event.

    The event intentionally exposes only a compact status, four clearance
    values, and a bounded number of visual outputs.  It can therefore be logged
    or forwarded by a channel adapter without exposing provider-specific sensor
    payloads.
    """
    selected_visuals = _selected_visuals(observation, max_visuals=max_visuals)
    messages = render_observation_messages(
        observation,
        max_visuals=max_visuals,
    )
    sink.set_observation_messages(messages)

    has_visuals = bool(selected_visuals)
    has_clearance = observation.base_clearance is not None
    available = has_visuals or has_clearance
    complete = has_visuals and has_clearance
    status = "ok" if complete else "partial" if available else "unavailable"
    summary = observation.summary or _event_summary(
        has_visuals=has_visuals,
        has_clearance=has_clearance,
    )
    summary = _truncate(summary, max_chars=max_summary_chars)

    event: dict[str, Any] = {
        "type": "observation",
        "turn": max(0, int(turn)),
        "available": available,
        "success": available,
        "status": status,
        "vision_available": bool(observation.visuals),
        "vision_success": has_visuals,
        "base_clearance_available": has_clearance,
        "base_clearance_success": has_clearance,
        "n_images": len(selected_visuals),
        "summary": summary,
        "base_clearance": (
            observation.base_clearance.as_dict()
            if observation.base_clearance is not None
            else {}
        ),
        "visual_outputs": [item.visual_output() for item in selected_visuals],
    }
    omitted = len(observation.visuals) - len(selected_visuals)
    if omitted:
        event["visual_outputs_omitted"] = omitted
    if observation.captured_at not in (None, ""):
        event["captured_at"] = observation.captured_at
    if observation.provenance:
        event["provenance"] = observation.provenance
    return event


def _selected_visuals(
    observation: Observation,
    *,
    max_visuals: int,
) -> tuple[VisualEvidence, ...]:
    if isinstance(max_visuals, bool) or not isinstance(max_visuals, int):
        raise TypeError("max_visuals must be an integer")
    if max_visuals < 0:
        raise ValueError("max_visuals must be non-negative")
    return observation.visuals[:max_visuals]


def _render_observation_text(
    observation: Observation,
    *,
    selected_visual_count: int,
) -> str:
    lines = ["Current Observation (valid for this decision only)."]
    if observation.summary:
        lines.append(observation.summary)
    if observation.captured_at not in (None, ""):
        lines.append(f"Captured at: {observation.captured_at}")
    if observation.provenance:
        lines.append(f"Provenance: {observation.provenance}")
    if observation.base_clearance is not None:
        values = observation.base_clearance.as_dict()
        lines.append(
            "Base clearance: "
            + ", ".join(
                f"{name.removesuffix('_m')}={_format_number(value)} m"
                for name, value in values.items()
            )
            + "."
        )
    if selected_visual_count:
        lines.append(
            "Visual evidence: "
            + ", ".join(
                item.name for item in observation.visuals[:selected_visual_count]
            )
            + "."
        )
    return "\n".join(lines)


def _event_summary(*, has_visuals: bool, has_clearance: bool) -> str:
    available: list[str] = []
    if has_visuals:
        available.append("visual evidence")
    if has_clearance:
        available.append("four-direction base clearance")
    if not available:
        return "No current Observation evidence is available."
    return "Current " + " and ".join(available) + " are available."


def _format_number(value: int | float) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _truncate(value: str, *, max_chars: int) -> str:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_summary_chars must be an integer")
    if max_chars < 1:
        raise ValueError("max_summary_chars must be positive")
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def _bounded_text(
    value: str,
    *,
    field_name: str,
    limit: int,
    allow_empty: bool = False,
) -> str:
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"VisualEvidence.{field_name} must not be empty")
    if len(text) > limit:
        raise ValueError(
            f"VisualEvidence.{field_name} must contain at most {limit} characters"
        )
    return text


__all__ = [
    "BaseClearance",
    "DEFAULT_MAX_OBSERVATION_SUMMARY_CHARS",
    "DEFAULT_MAX_OBSERVATION_VISUALS",
    "Observation",
    "VisualEvidence",
    "publish_observation",
    "render_observation_messages",
]
