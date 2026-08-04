"""Default base-motion safety filter for pre-execution interception."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

from harness.context import ObservationSink, ToolCall
from harness.tools.registry import ToolRegistry
from harness.world.observation import (
    BaseClearance,
    Observation,
    render_observation_messages,
)

BASE_MOTION_TOOLS = {"move_base", "navigate_to"}
TRANSLATION_DIRECTIONS = {"forward", "backward", "left", "right"}
BASE_CLEARANCE_SOURCE = "base_clearance_provider"
DEFAULT_CLEARANCE_MARGIN_M = 0.05
MIN_MOTION_INCREMENT_M = 0.03


@dataclass(frozen=True)
class PreExecutionHookResult:
    """Result of deterministic pre-execution interception for one Tool Call."""

    call: ToolCall
    events: tuple[dict[str, Any], ...] = ()
    blocked_result: dict[str, Any] | None = None


class PreExecutionHook(Protocol):
    """One deterministic hook applied before model-selected execution."""

    def apply(
        self,
        call: ToolCall,
        *,
        registry: ToolRegistry,
        turn: int,
    ) -> PreExecutionHookResult: ...


class BaseClearanceProvider(Protocol):
    """Capture fresh four-direction clearance without exposing a Tool."""

    def __call__(self) -> BaseClearance: ...


class BaseMotionSafetyFilter:
    """Refresh model-visible clearance and constrain base translations."""

    def __init__(
        self,
        *,
        clearance_provider: BaseClearanceProvider | None = None,
        margin_m: float = DEFAULT_CLEARANCE_MARGIN_M,
    ) -> None:
        normalized_margin = float(margin_m)
        if not math.isfinite(normalized_margin) or normalized_margin < 0:
            raise ValueError(
                "base-motion safety margin must be finite and non-negative"
            )
        self.margin_m = normalized_margin
        self.clearance_provider = clearance_provider

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        *,
        clearance_provider: BaseClearanceProvider | None = None,
    ) -> BaseMotionSafetyFilter:
        safety = config.get("safety")
        safety = safety if isinstance(safety, dict) else {}
        return cls(
            clearance_provider=clearance_provider,
            margin_m=float(
                safety.get(
                    "base_clearance_margin_m",
                    DEFAULT_CLEARANCE_MARGIN_M,
                )
            ),
        )

    def apply(
        self,
        call: ToolCall,
        *,
        registry: ToolRegistry,
        turn: int,
    ) -> PreExecutionHookResult:
        if not self._requires_base_clearance(call):
            return PreExecutionHookResult(call)

        started = time.monotonic()
        del registry
        clearance, failure_reason = self._capture_clearance()
        event = {
            "type": "safety_check",
            "turn": turn,
            "phase": "before_execution",
            "source": BASE_CLEARANCE_SOURCE,
            "success": clearance is not None,
            "base_clearance": clearance.as_dict() if clearance is not None else {},
            "duration_ms": int((time.monotonic() - started) * 1000),
            "pre_execution_hook": True,
            "triggered_by": call.name,
        }
        if failure_reason:
            event["reason"] = failure_reason
        if clearance is None:
            return PreExecutionHookResult(
                call,
                (event,),
                {
                    "success": False,
                    "reason": (
                        "Base motion blocked because fresh BaseClearance "
                        "capture failed: "
                        f"{failure_reason or 'unknown error'}"
                    ),
                    "pre_execution_hook": "base_motion_safety_filter",
                },
            )

        request_error = self._translation_request_error(call)
        if request_error:
            return PreExecutionHookResult(
                call,
                (event,),
                {
                    "success": False,
                    "reason": request_error,
                    "pre_execution_hook": "base_motion_safety_filter",
                },
            )

        blocked_reason = self._blocked_base_motion_reason(call, clearance)
        if blocked_reason:
            return PreExecutionHookResult(
                call,
                (event,),
                {
                    "success": False,
                    "reason": blocked_reason,
                    "pre_execution_hook": "base_motion_safety_filter",
                },
            )

        rewritten = self._clamp_translation(call, clearance)
        if rewritten.arguments != call.arguments:
            event["rewritten_arguments"] = dict(rewritten.arguments)
        return PreExecutionHookResult(rewritten, (event,))

    def refresh_decision_context(
        self,
        sink: ObservationSink,
        *,
        registry: ToolRegistry,
        turn: int,
    ) -> dict[str, Any] | None:
        """Put fresh four-direction clearance into Refreshed context.

        The model-facing reading supports planning only. Executable base
        motion calls :meth:`apply`, which captures clearance again immediately
        before the action and enforces the result independently.
        """
        if not any(registry.has(name) for name in BASE_MOTION_TOOLS):
            return None

        started = time.monotonic()
        clearance, failure_reason = self._capture_clearance()
        event: dict[str, Any] = {
            "type": "safety_check",
            "turn": turn,
            "phase": "before_model_decision",
            "source": BASE_CLEARANCE_SOURCE,
            "success": clearance is not None,
            "base_clearance": clearance.as_dict() if clearance is not None else {},
            "duration_ms": int((time.monotonic() - started) * 1000),
            "context_lifetime": "refreshed",
        }
        if failure_reason:
            event["reason"] = failure_reason
        if clearance is None:
            return event

        sink.add_observation_messages(
            render_observation_messages(
                Observation(base_clearance=clearance),
            )
        )
        return event

    def _capture_clearance(self) -> tuple[BaseClearance | None, str]:
        provider = self.clearance_provider
        if provider is None:
            return None, "No BaseClearance provider is installed."
        try:
            clearance = provider()
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"
        if not isinstance(clearance, BaseClearance):
            return None, "BaseClearance provider returned an invalid value."
        return clearance, ""

    @staticmethod
    def _translation_request_error(call: ToolCall) -> str:
        if call.name != "move_base":
            return ""
        direction = str(call.arguments.get("direction") or "")
        if direction not in TRANSLATION_DIRECTIONS:
            return ""
        requested = call.arguments.get("distance_m")
        if requested in (None, ""):
            return ""
        if isinstance(requested, bool):
            return "Base motion blocked because distance_m is not a valid distance."
        try:
            requested_m = float(requested)
        except (TypeError, ValueError):
            return "Base motion blocked because distance_m is not a valid distance."
        if not math.isfinite(requested_m) or requested_m < 0:
            return (
                "Base motion blocked because distance_m must be finite and "
                "non-negative."
            )
        return ""

    def _blocked_base_motion_reason(
        self,
        call: ToolCall,
        clearance: BaseClearance,
    ) -> str:
        if call.name == "navigate_to":
            clearances = {
                direction: _directional_clearance(clearance, direction)
                for direction in TRANSLATION_DIRECTIONS
            }
            if all(
                value < self.margin_m + MIN_MOTION_INCREMENT_M
                for value in clearances.values()
            ):
                values = ", ".join(
                    f"{direction}={value:.3f} m"
                    for direction, value in sorted(clearances.items())
                )
                return (
                    "Navigation blocked because no direction has enough "
                    "immediate base clearance "
                    f"({values}; margin={self.margin_m:.3f} m)."
                )
            return ""

        if call.name != "move_base":
            return ""
        direction = str(call.arguments.get("direction") or "")
        if direction not in TRANSLATION_DIRECTIONS:
            return ""
        distance = _directional_clearance(clearance, direction)
        if distance - self.margin_m >= MIN_MOTION_INCREMENT_M:
            return ""
        return (
            f"Base motion blocked by fresh {direction} clearance "
            f"({distance:.3f} m with {self.margin_m:.3f} m margin)."
        )

    @staticmethod
    def _requires_base_clearance(call: ToolCall) -> bool:
        if call.name not in BASE_MOTION_TOOLS:
            return False
        if call.name == "move_base":
            return str(call.arguments.get("mode") or "").strip() != "check_only"
        return bool(str(call.arguments.get("target") or "").strip())

    def _clamp_translation(
        self,
        call: ToolCall,
        clearance: BaseClearance,
    ) -> ToolCall:
        if call.name != "move_base":
            return call
        direction = str(call.arguments.get("direction") or "")
        if direction not in TRANSLATION_DIRECTIONS:
            return call
        requested = call.arguments.get("distance_m")
        if requested in (None, ""):
            return call
        try:
            requested_m = float(requested)
        except (TypeError, ValueError):
            return call
        distance = _directional_clearance(clearance, direction)
        admissible = max(0.0, distance - self.margin_m)
        if admissible < MIN_MOTION_INCREMENT_M:
            return call
        clamped = min(requested_m, admissible)
        if clamped >= requested_m:
            return call
        arguments = dict(call.arguments)
        arguments["distance_m"] = round(clamped, 3)
        return ToolCall(call.id, call.name, arguments)


def _directional_clearance(
    clearance: BaseClearance,
    direction: str,
) -> float:
    return float(getattr(clearance, f"{direction}_m"))


__all__ = [
    "BaseClearanceProvider",
    "BaseMotionSafetyFilter",
    "PreExecutionHook",
    "PreExecutionHookResult",
]
