"""Benchmark-neutral simulation boundary for the Thea Harness.

Simulation environments expose low-level actions, while the Agentic Loop
selects named tools. This module keeps those concerns separate:

- :class:`SimulationEpisode` normalizes reset, observation, stepping, and task
  success across benchmarks.
- :class:`SimulationToolSpec` maps one model-visible primitive tool to a
  benchmark policy or action chunk.
- :class:`SimulationRuntime` publishes current Observation, registers the
  tools, and retains post-execution evidence for the hidden evaluator.

Heavy simulator packages remain deployment-owned optional dependencies.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from harness.context import ObservationSink
from harness.evaluation.contract import EvaluationRequest, EvaluatorVerdict
from harness.tools.registry import BuiltinTool, ToolRegistry
from harness.world.observation import Observation, publish_observation


@dataclass(frozen=True, slots=True)
class SimulationTransition:
    """Normalized result of one low-level simulator step."""

    observation: Mapping[str, Any]
    reward: float | None = None
    terminated: bool = False
    truncated: bool = False
    task_success: bool = False
    info: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class SimulationEpisode(Protocol):
    """Minimal episode API implemented by each benchmark adapter."""

    @property
    def benchmark(self) -> str: ...

    @property
    def task_id(self) -> str: ...

    @property
    def instruction(self) -> str: ...

    @property
    def last_transition(self) -> SimulationTransition | None: ...

    def reset(self, *, seed: int | None = None) -> Mapping[str, Any]: ...

    def observe(self) -> Mapping[str, Any]: ...

    def step(self, action: Any) -> SimulationTransition: ...

    def close(self) -> None: ...


SimulationObservationProjector = Callable[[Mapping[str, Any], int], Observation]
SimulationToolExecutor = Callable[
    [SimulationEpisode, Mapping[str, Any]],
    Mapping[str, Any],
]
SimulationActionPolicy = Callable[
    [SimulationEpisode, Mapping[str, Any]],
    Iterable[Any],
]


@dataclass(frozen=True, slots=True)
class SimulationToolSpec:
    """One Thea primitive tool backed by a simulator policy."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    executor: SimulationToolExecutor
    post_condition: str

    def __post_init__(self) -> None:
        for field_name in ("name", "description", "post_condition"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"SimulationToolSpec.{field_name} must not be empty")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "input_schema", dict(self.input_schema))


@dataclass(frozen=True, slots=True)
class SimulationRunEvidence:
    """Post-execution evidence retained under one tool run ID."""

    run_id: str
    benchmark: str
    task_id: str
    instruction: str
    observation: Mapping[str, Any]
    task_success: bool
    terminated: bool
    truncated: bool
    tool_result: Mapping[str, Any]


class SimulationRuntime:
    """Bridge one simulation episode into Harness provider boundaries."""

    def __init__(
        self,
        episode: SimulationEpisode,
        *,
        observation_projector: SimulationObservationProjector,
        tools: Iterable[SimulationToolSpec],
        max_run_evidence: int = 64,
    ) -> None:
        if max_run_evidence < 1:
            raise ValueError("max_run_evidence must be positive")
        self.episode = episode
        self.observation_projector = observation_projector
        self.tools = tuple(tools)
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Simulation tool names must be unique")
        self.max_run_evidence = max_run_evidence
        self._run_evidence: OrderedDict[str, SimulationRunEvidence] = OrderedDict()
        self._closed = False

    def reset(self, *, seed: int | None = None) -> Mapping[str, Any]:
        """Reset the active benchmark episode."""
        self._require_open()
        self._run_evidence.clear()
        return self.episode.reset(seed=seed)

    def register_tools(self, registry: ToolRegistry) -> None:
        """Register all simulator-backed primitive tools."""
        self._require_open()
        for spec in self.tools:
            registry.register(
                BuiltinTool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=dict(spec.input_schema),
                    fn=self._tool_callable(spec),
                    post_condition=spec.post_condition,
                )
            )

    def observation_provider(
        self,
        sink: ObservationSink,
        turn: int,
    ) -> dict[str, Any]:
        """Project the latest simulator state into current Observation."""
        self._require_open()
        observation = self.observation_projector(self.episode.observe(), turn)
        if not isinstance(observation, Observation):
            raise TypeError(
                "Simulation observation_projector must return harness.Observation"
            )
        return publish_observation(sink, observation, turn=turn)

    def capture(self, run_id: str) -> SimulationRunEvidence:
        """Return evidence for the hidden post-execution evaluator."""
        normalized = str(run_id or "").strip()
        if normalized not in self._run_evidence:
            raise KeyError(f"Unknown simulation run_id: {normalized!r}")
        return self._run_evidence[normalized]

    def close(self) -> None:
        """Close the deployment-owned simulator episode once."""
        if self._closed:
            return
        self._closed = True
        self.episode.close()
        self._run_evidence.clear()

    def _tool_callable(self, spec: SimulationToolSpec) -> Callable[..., dict[str, Any]]:
        def execute(**arguments: Any) -> dict[str, Any]:
            self._require_open()
            result = dict(spec.executor(self.episode, arguments))
            run_id = str(result.get("run_id") or uuid.uuid4().hex).strip()
            if not run_id:
                run_id = uuid.uuid4().hex
            result["run_id"] = run_id

            transition = self.episode.last_transition
            evidence = SimulationRunEvidence(
                run_id=run_id,
                benchmark=self.episode.benchmark,
                task_id=self.episode.task_id,
                instruction=self.episode.instruction,
                observation=self.episode.observe(),
                task_success=bool(
                    result.get("task_success")
                    if "task_success" in result
                    else transition and transition.task_success
                ),
                terminated=bool(
                    result.get("terminated")
                    if "terminated" in result
                    else transition and transition.terminated
                ),
                truncated=bool(
                    result.get("truncated")
                    if "truncated" in result
                    else transition and transition.truncated
                ),
                tool_result=dict(result),
            )
            self._run_evidence[run_id] = evidence
            self._run_evidence.move_to_end(run_id)
            while len(self._run_evidence) > self.max_run_evidence:
                self._run_evidence.popitem(last=False)
            return result

        return execute

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("SimulationRuntime is closed")


class SimulationTaskEvaluator:
    """Map benchmark task status to Evaluation-as-Exit-Codes verdicts."""

    def evaluate(self, request: EvaluationRequest) -> EvaluatorVerdict:
        evidence = request.post_execution_observation
        if not isinstance(evidence, SimulationRunEvidence):
            raise TypeError("SimulationTaskEvaluator requires SimulationRunEvidence")
        if evidence.task_success:
            return EvaluatorVerdict(
                status="success",
                evidence=(
                    f"{evidence.benchmark} reports task success.",
                    f"Task: {evidence.task_id}",
                ),
            )
        if evidence.terminated or evidence.truncated:
            return EvaluatorVerdict(
                status="failure",
                evidence=(f"Task: {evidence.task_id}",),
                failure_reason=(
                    "The simulation episode ended before its success condition "
                    "was satisfied."
                ),
            )
        return EvaluatorVerdict(
            status="process",
            evidence=(
                "The simulator accepted the action and the episode remains active.",
            ),
        )


def action_chunk_tool(
    *,
    name: str,
    description: str,
    input_schema: Mapping[str, Any],
    post_condition: str,
    policy: SimulationActionPolicy,
    max_actions: int | None = None,
) -> SimulationToolSpec:
    """Build one primitive tool from a policy that yields low-level actions."""
    if max_actions is not None and max_actions < 1:
        raise ValueError("max_actions must be positive when provided")

    def execute(
        episode: SimulationEpisode,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        count = 0
        last_transition: SimulationTransition | None = None
        for action in policy(episode, arguments):
            if max_actions is not None and count >= max_actions:
                break
            last_transition = episode.step(action)
            count += 1
            if (
                last_transition.task_success
                or last_transition.terminated
                or last_transition.truncated
            ):
                break

        if count == 0:
            return {
                "success": False,
                "reason": "The simulation policy produced no actions.",
                "kind": "empty_action_chunk",
            }
        assert last_transition is not None
        return {
            "success": True,
            "observation": f"Executed {count} simulator action(s).",
            "actions_executed": count,
            "reward": last_transition.reward,
            "task_success": last_transition.task_success,
            "terminated": last_transition.terminated,
            "truncated": last_transition.truncated,
        }

    return SimulationToolSpec(
        name=name,
        description=description,
        input_schema=input_schema,
        executor=execute,
        post_condition=post_condition,
    )


__all__ = [
    "SimulationActionPolicy",
    "SimulationEpisode",
    "SimulationObservationProjector",
    "SimulationRunEvidence",
    "SimulationRuntime",
    "SimulationTaskEvaluator",
    "SimulationToolExecutor",
    "SimulationToolSpec",
    "SimulationTransition",
    "action_chunk_tool",
]
