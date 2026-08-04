"""Stable dependency contracts for the provider-neutral harness."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from harness.context import Context, ModelResponse, ObservationSink
from harness.tools.registry import ToolRegistry
from harness.world.scene_graph import SceneGraphBrief


@runtime_checkable
class ModelProtocol(Protocol):
    """Model facade consumed by the Agentic Loop."""

    def call(
        self,
        ctx: Context,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse: ...


@runtime_checkable
class TaskNotesProtocol(Protocol):
    """Task-local working record consumed by the loop."""

    def reset(self, goal: str) -> None: ...

    def revise_goal(self, goal: str) -> None: ...

    def snapshot_message_if_changed(self) -> dict[str, Any] | None: ...

    def append_tool_result(
        self,
        *,
        tool: str,
        result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
        evaluator_verdict: dict[str, Any] | None = None,
    ) -> None: ...

    def expire(self) -> None: ...


@runtime_checkable
class MemoryProtocol(Protocol):
    """Memory lifecycle boundary used at task start, during turns, and task end."""

    task_notes: TaskNotesProtocol

    def resident_memory(self) -> str: ...

    def tool_experience_summaries(
        self,
        tool_names: Iterable[str],
    ) -> dict[str, str]: ...

    def consolidate(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]: ...


@runtime_checkable
class SceneGraphProtocol(Protocol):
    """Persistent world-state boundary used by the Agentic Loop."""

    def refresh_from_perception(self) -> None: ...

    def brief(self) -> SceneGraphBrief: ...

    def apply_confirmed_execution(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        evaluator_verdict: dict[str, Any],
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class MCPClientProtocol(Protocol):
    """Minimal MCP client surface required by the harness."""

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class RuntimeResource(Protocol):
    """Deployment resource whose lifetime is owned by one Harness."""

    def close(self) -> None: ...


@runtime_checkable
class ObservationProvider(Protocol):
    """Capture current evidence and replace the previous Observation."""

    def __call__(
        self,
        sink: ObservationSink,
        turn: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class PromptSnapshotBuilder(Protocol):
    """Build one payload-safe observability snapshot of model context."""

    def __call__(
        self,
        *,
        turn: int,
        provider_system_content: str,
        tool_definitions: list[dict[str, Any]],
        accumulated_messages: list[dict[str, Any]],
        loaded_skill_context_blocks: list[dict[str, Any]],
        observation_messages: list[dict[str, Any]],
        resident_context: str,
        refreshed_context: str,
        transient_tool_result_messages: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


ToolRegistrar = Callable[[ToolRegistry], None]


__all__ = [
    "MCPClientProtocol",
    "MemoryProtocol",
    "ModelProtocol",
    "ObservationProvider",
    "PromptSnapshotBuilder",
    "RuntimeResource",
    "SceneGraphProtocol",
    "TaskNotesProtocol",
    "ToolRegistrar",
]
