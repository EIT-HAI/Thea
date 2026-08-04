"""LIBERO adapter for the benchmark-neutral simulation boundary."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from harness.world.observation import Observation, VisualEvidence

from thea_simulation.runtime import SimulationTransition

LiberoImageEncoder = Callable[[Any], bytes]


def project_libero_observation(
    raw_observation: Mapping[str, Any],
    turn: int,
    *,
    image_encoder: LiberoImageEncoder | None = None,
) -> Observation:
    """Project every LIBERO RGB camera field into current visual evidence.

    LIBERO names rendered RGB arrays with an ``_image`` suffix, including
    ``agentview_image`` and ``robot0_eye_in_hand_image``.  The projector keeps
    every such HxWx3 field instead of selecting one preferred camera.  State,
    depth, and proprioceptive arrays remain outside the visual projection.

    ``image_encoder`` is injectable for deployments that need a different
    image codec.  The default rotates LIBERO's raw camera arrays into their
    displayed orientation and encodes lossless PNG bytes with OpenCV.
    """
    encoder = image_encoder or _encode_libero_rgb_png
    visuals: list[VisualEvidence] = []
    for key in sorted(raw_observation):
        value = raw_observation[key]
        shape = getattr(value, "shape", ())
        if (
            not str(key).endswith("_image")
            or not isinstance(shape, tuple)
            or len(shape) != 3
            or shape[-1] != 3
        ):
            continue
        name = str(key).removesuffix("_image")
        visuals.append(
            VisualEvidence.from_bytes(
                name,
                encoder(value),
                media_type="image/png",
                caption=f"Current LIBERO {name} camera observation.",
                freshness="current",
            )
        )
    if not visuals:
        available = ", ".join(sorted(str(key) for key in raw_observation))
        raise ValueError(
            "LIBERO observation contains no HxWx3 *_image fields. "
            f"Available keys: {available}"
        )
    return Observation(
        visuals=tuple(visuals),
        captured_at=turn,
        provenance="libero",
        summary=f"Current LIBERO observation from {len(visuals)} camera(s).",
    )


def _encode_libero_rgb_png(raw_image: Any) -> bytes:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "The default LIBERO image projector requires NumPy and OpenCV. "
            "Install LIBERO's runtime dependencies or pass image_encoder."
        ) from exc

    image = np.asarray(raw_image)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(
            f"Expected an HxWx3 LIBERO RGB image, received {image.shape!r}"
        )
    upright_rgb = np.ascontiguousarray(image[::-1])
    bgr = cv2.cvtColor(upright_rgb, cv2.COLOR_RGB2BGR)
    encoded, payload = cv2.imencode(".png", bgr)
    if not encoded:
        raise RuntimeError("OpenCV failed to encode a LIBERO camera image")
    return payload.tobytes()


class LiberoEpisode:
    """Wrap a LIBERO ``OffScreenRenderEnv`` as one simulation episode.

    The constructor accepts an already-created environment so importing this
    module never requires LIBERO. Use :meth:`from_suite` when LIBERO is
    installed in the active environment.
    """

    def __init__(
        self,
        env: Any,
        *,
        task_id: str,
        instruction: str,
        initial_state: Any | None = None,
    ) -> None:
        self._env = env
        self._task_id = str(task_id)
        self._instruction = str(instruction).strip()
        self._initial_state = initial_state
        self._observation: Mapping[str, Any] | None = None
        self._last_transition: SimulationTransition | None = None
        self._closed = False

    @classmethod
    def from_suite(
        cls,
        suite_name: str,
        task_id: int,
        *,
        initial_state_id: int = 0,
        seed: int = 0,
        env_kwargs: Mapping[str, Any] | None = None,
    ) -> LiberoEpisode:
        """Create an episode through LIBERO's documented benchmark API."""
        try:
            from libero.libero import benchmark, get_libero_path
            from libero.libero.envs import OffScreenRenderEnv
        except ImportError as exc:
            raise RuntimeError(
                "LIBERO is not installed. Install the official LIBERO repository "
                "in its supported environment before using from_suite()."
            ) from exc

        benchmark_types = benchmark.get_benchmark_dict()
        normalized_suite = str(suite_name).strip().lower()
        if normalized_suite not in benchmark_types:
            available = ", ".join(sorted(benchmark_types))
            raise ValueError(
                f"Unknown LIBERO suite {normalized_suite!r}. Available: {available}"
            )
        suite = benchmark_types[normalized_suite]()
        task = suite.get_task(int(task_id))
        bddl_file = os.path.join(
            get_libero_path("bddl_files"),
            task.problem_folder,
            task.bddl_file,
        )
        kwargs = dict(env_kwargs or {})
        kwargs["bddl_file_name"] = bddl_file
        env = OffScreenRenderEnv(**kwargs)
        env.seed(int(seed))
        init_states = suite.get_task_init_states(int(task_id))
        try:
            initial_state = init_states[int(initial_state_id)]
        except IndexError as exc:
            raise ValueError(
                f"LIBERO initial_state_id {initial_state_id} is out of range"
            ) from exc
        return cls(
            env,
            task_id=f"{normalized_suite}:{task_id}:{initial_state_id}",
            instruction=task.language,
            initial_state=initial_state,
        )

    @property
    def benchmark(self) -> str:
        return "libero"

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
        if seed is not None:
            self._env.seed(int(seed))
        observation = self._env.reset()
        if self._initial_state is not None:
            observation = self._env.set_init_state(self._initial_state)
        self._observation = _require_mapping(observation, operation="LIBERO reset")
        self._last_transition = None
        return self._observation

    def observe(self) -> Mapping[str, Any]:
        self._require_open()
        if self._observation is None:
            raise RuntimeError("Reset the LIBERO episode before observing it")
        return self._observation

    def step(self, action: Any) -> SimulationTransition:
        self._require_open()
        payload = self._env.step(action)
        if not isinstance(payload, tuple) or len(payload) not in (4, 5):
            raise TypeError("LIBERO env.step() must return a four- or five-item tuple")
        if len(payload) == 4:
            observation, reward, done, info = payload
            terminated = bool(done)
            truncated = False
        else:
            observation, reward, terminated, truncated, info = payload
            terminated = bool(terminated)
            truncated = bool(truncated)
        self._observation = _require_mapping(
            observation,
            operation="LIBERO step",
        )
        success = bool(self._env.check_success())
        self._last_transition = SimulationTransition(
            observation=self._observation,
            reward=float(reward),
            terminated=terminated,
            truncated=truncated,
            task_success=success,
            info=dict(info) if isinstance(info, Mapping) else {},
        )
        return self._last_transition

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._env.close()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("LIBERO episode is closed")


def _require_mapping(value: Any, *, operation: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{operation} must return a mapping observation")
    return value


__all__ = ["LiberoEpisode", "LiberoImageEncoder", "project_libero_observation"]
