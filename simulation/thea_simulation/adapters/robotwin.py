"""RoboTwin adapter for the benchmark-neutral simulation boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from harness.world.observation import Observation, VisualEvidence

from thea_simulation.runtime import SimulationTransition

RoboTwinSetup = Callable[[Any, int | None], Mapping[str, Any] | None]
RoboTwinImageEncoder = Callable[[Any], bytes]


def project_robotwin_observation(
    raw_observation: Mapping[str, Any],
    turn: int,
    *,
    image_encoder: RoboTwinImageEncoder | None = None,
) -> Observation:
    """Project every RoboTwin RGB camera into current visual evidence.

    RoboTwin stores model-visible cameras under
    ``observation.<camera_name>.rgb`` and may additionally expose an observer
    image as ``third_view_rgb``. The projector preserves every available RGB
    view and leaves camera calibration, robot state, depth, and point clouds
    outside the visual projection.
    """
    encoder = image_encoder or _encode_robotwin_rgb_png
    camera_root = raw_observation.get("observation")
    visuals: list[VisualEvidence] = []
    if isinstance(camera_root, Mapping):
        for camera_name in sorted(camera_root):
            camera_data = camera_root[camera_name]
            if not isinstance(camera_data, Mapping) or "rgb" not in camera_data:
                continue
            raw_image = camera_data["rgb"]
            if not _is_rgb_image(raw_image):
                continue
            visuals.append(
                VisualEvidence.from_bytes(
                    str(camera_name),
                    encoder(raw_image),
                    media_type="image/png",
                    caption=f"Current RoboTwin {camera_name} observation.",
                    freshness="current",
                )
            )

    third_view = raw_observation.get("third_view_rgb")
    if _is_rgb_image(third_view):
        visuals.append(
            VisualEvidence.from_bytes(
                "third_view",
                encoder(third_view),
                media_type="image/png",
                caption="Current RoboTwin third-person observation.",
                freshness="current",
            )
        )

    if not visuals:
        available = ", ".join(sorted(str(key) for key in raw_observation))
        raise ValueError(
            "RoboTwin observation contains no HxWx3 RGB camera fields. "
            f"Available top-level keys: {available}"
        )
    return Observation(
        visuals=tuple(visuals),
        captured_at=turn,
        provenance="robotwin",
        summary=f"Current RoboTwin observation from {len(visuals)} camera(s).",
    )


def _is_rgb_image(value: Any) -> bool:
    shape = getattr(value, "shape", ())
    return isinstance(shape, tuple) and len(shape) == 3 and shape[-1] == 3


def _encode_robotwin_rgb_png(raw_image: Any) -> bytes:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "The default RoboTwin image projector requires NumPy and OpenCV. "
            "Install RoboTwin's runtime dependencies or pass image_encoder."
        ) from exc

    image = np.asarray(raw_image)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(
            "RoboTwin RGB camera fields must have shape HxWx3; "
            f"received {image.shape!r}"
        )
    if image.dtype != np.uint8:
        if np.issubdtype(image.dtype, np.floating) and image.size:
            maximum = float(np.nanmax(image))
            if maximum <= 1.0:
                image = image * 255.0
        image = np.clip(image, 0, 255).astype(np.uint8)
    encoded_ok, encoded = cv2.imencode(
        ".png",
        cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
    )
    if not encoded_ok:
        raise RuntimeError("OpenCV could not encode the RoboTwin RGB camera")
    return bytes(encoded)


class RoboTwinEpisode:
    """Wrap one initialized RoboTwin task environment.

    RoboTwin task construction depends on the selected task configuration and
    embodiment assets. ``setup`` localizes that deployment-specific
    ``setup_demo`` call while this adapter normalizes the stable
    ``get_obs / take_action / check_success`` evaluation API.
    """

    def __init__(
        self,
        env: Any,
        *,
        task_id: str,
        instruction: str,
        setup: RoboTwinSetup | None = None,
        action_type: str = "qpos",
    ) -> None:
        normalized_action_type = str(action_type).strip()
        if normalized_action_type not in {"qpos", "ee", "delta_ee"}:
            raise ValueError("RoboTwin action_type must be qpos, ee, or delta_ee")
        self._env = env
        self._task_id = str(task_id)
        self._instruction = str(instruction).strip()
        self._setup = setup
        self._action_type = normalized_action_type
        self._observation: Mapping[str, Any] | None = None
        self._last_transition: SimulationTransition | None = None
        self._closed = False

    @property
    def benchmark(self) -> str:
        return "robotwin"

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def instruction(self) -> str:
        return self._instruction

    @property
    def last_transition(self) -> SimulationTransition | None:
        return self._last_transition

    def reset(self, *, seed: int | None = None) -> Mapping[str, Any]:
        self._require_open()
        observation = self._setup(self._env, seed) if self._setup else None
        if hasattr(self._env, "set_instruction"):
            self._env.set_instruction(instruction=self._instruction)
        if observation is None:
            observation = self._env.get_obs()
        self._observation = _require_mapping(
            observation,
            operation="RoboTwin setup/get_obs",
        )
        self._last_transition = None
        return self._observation

    def observe(self) -> Mapping[str, Any]:
        self._require_open()
        if self._observation is None:
            raise RuntimeError("Reset the RoboTwin episode before observing it")
        return self._observation

    def step(self, action: Any) -> SimulationTransition:
        self._require_open()
        self._env.take_action(action, action_type=self._action_type)
        self._observation = _require_mapping(
            self._env.get_obs(),
            operation="RoboTwin get_obs",
        )
        success = bool(
            getattr(self._env, "eval_success", False) or self._env.check_success()
        )
        step_count = int(getattr(self._env, "take_action_cnt", 0))
        step_limit = getattr(self._env, "step_lim", None)
        truncated = bool(
            step_limit is not None and step_count >= int(step_limit) and not success
        )
        self._last_transition = SimulationTransition(
            observation=self._observation,
            reward=1.0 if success else 0.0,
            terminated=success,
            truncated=truncated,
            task_success=success,
            info={
                "step_count": step_count,
                "step_limit": step_limit,
            },
        )
        return self._last_transition

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if hasattr(self._env, "close_env"):
            self._env.close_env()
        elif hasattr(self._env, "close"):
            self._env.close()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("RoboTwin episode is closed")


def _require_mapping(value: Any, *, operation: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{operation} must return a mapping observation")
    return value


__all__ = [
    "RoboTwinEpisode",
    "RoboTwinImageEncoder",
    "RoboTwinSetup",
    "project_robotwin_observation",
]
