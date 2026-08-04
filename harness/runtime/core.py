"""Composition root for the provider-neutral Thea harness.

The :class:`Harness` wires the model, Context, Tool Registry, hooks, Memory,
Observation, Scene Graph, Skill System, and provider boundaries.
:mod:`harness.runtime.agentic_loop` owns task-scoped control flow.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from harness.configuration.runtime import ConfigurationError, validate_runtime_config
from harness.context import (
    TASK_NOTES_MESSAGE_KIND,
    Context,
    ModelResponse,
    ObservationSink,
    ToolCall,
)
from harness.context.compaction import ContextCompactor
from harness.context.rendering import (
    build_refreshed_context,
    build_resident_context,
)
from harness.context.snapshots import build_prompt_snapshot
from harness.context.system_prompt import SYSTEM_PROMPT, extract_system_prompt
from harness.evaluation.contract import (
    EVALUATE_RUN_TOOL_NAME,
    EvaluatorProtocol,
    EvaluatorTool,
    PostExecutionObservationProvider,
)
from harness.mcp import MCPClient
from harness.models import ModelClient
from harness.protocols import (
    MCPClientProtocol,
    MemoryProtocol,
    ModelProtocol,
    ObservationProvider,
    PromptSnapshotBuilder,
    RuntimeResource,
    SceneGraphProtocol,
    ToolRegistrar,
)
from harness.runtime.agentic_loop import AgenticLoop
from harness.runtime.execution import ToolExecutionPipeline
from harness.runtime.hooks import ToolHooks
from harness.runtime.standalone import (
    NullMemory,
    NullSceneGraph,
    null_observation,
)
from harness.runtime.task import clear_task_scope, consolidate_task_notes_at_task_end
from harness.skills.discovery import (
    load_skills,
    render_skill_catalog,
    skills_dir_from_config,
)
from harness.skills.runtime import SkillSystem
from harness.tools.registry import MCPTool, ToolRegistry
from harness.world.embodiment import (
    load_embodiment_profile_document_from_config,
)
from harness.world.safety import BaseClearanceProvider
from harness.world.scene_graph import render_scene_graph_brief

logger = logging.getLogger(__name__)


class Harness(AgenticLoop):
    """Compose the boundaries consumed by the task-scoped Agentic Loop."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        model: ModelProtocol | None = None,
        memory: MemoryProtocol | None = None,
        observation_provider: ObservationProvider | None = None,
        base_clearance_provider: BaseClearanceProvider | None = None,
        registry: ToolRegistry | None = None,
        hooks: ToolHooks | None = None,
        mcp_client: MCPClientProtocol | None = None,
        scene_graph: SceneGraphProtocol | None = None,
        builtin_registrar: ToolRegistrar | None = None,
        prompt_snapshot_builder: PromptSnapshotBuilder | None = None,
        context_compactor: ContextCompactor | None = None,
        compaction_model: ModelProtocol | None = None,
        evaluator: EvaluatorProtocol | None = None,
        post_execution_observation_provider: (
            PostExecutionObservationProvider | None
        ) = None,
        owned_resources: Iterable[RuntimeResource] = (),
    ) -> None:
        """Create one harness from provider-neutral dependency boundaries."""
        self._task_lock = threading.Lock()
        self._task_active = False
        self._closed = False
        self._owned_resources = tuple(owned_resources)
        self._owns_mcp_client = mcp_client is None
        effective_mcp_client = mcp_client

        try:
            self.config = validate_runtime_config(config)
            if effective_mcp_client is None:
                effective_mcp_client = MCPClient(self.config.get("servers", []))
            self.mcp_client = effective_mcp_client
            self.model = (
                ModelClient(self.config.get("llm", {})) if model is None else model
            )
            self.compaction_model = compaction_model
            self.context_compactor = (
                ContextCompactor.from_config(self.config)
                if context_compactor is None
                else context_compactor
            )
            self.memory = NullMemory() if memory is None else memory
            self.scene_graph = NullSceneGraph() if scene_graph is None else scene_graph
            self.observation_provider = (
                null_observation
                if observation_provider is None
                else observation_provider
            )
            self.prompt_snapshot_builder = (
                build_prompt_snapshot
                if prompt_snapshot_builder is None
                else prompt_snapshot_builder
            )

            embodiment_profile = load_embodiment_profile_document_from_config(
                self.config
            )
            self.embodiment_profile = (
                "" if embodiment_profile is None else embodiment_profile.render()
            )
            self.skills = load_skills(skills_dir_from_config(self.config))
            self.skill_system = SkillSystem(self.skills)
            self.skill_catalog = render_skill_catalog(self.skills)

            self.registry = ToolRegistry() if registry is None else registry
            self.skill_system.register_tools(self.registry)
            if builtin_registrar is not None:
                builtin_registrar(self.registry)
            self._register_mcp_tools()
            self._register_evaluator_tool(
                evaluator=evaluator,
                observation_provider=post_execution_observation_provider,
            )

            if hooks is not None and base_clearance_provider is not None:
                raise ConfigurationError(
                    "base_clearance_provider cannot be combined with custom hooks; "
                    "attach it to the custom pre-execution hook instead."
                )
            self.hooks = (
                ToolHooks(
                    self.registry,
                    self.config,
                    base_clearance_provider=base_clearance_provider,
                )
                if hooks is None
                else hooks
            )
            self.hooks.bind_registry(self.registry)

            self.ctx: Context | None = None
            self._visible_tool_definitions: list[dict[str, Any]] = []
            self._loaded_skill_context_metadata: list[dict[str, Any]] = []
            self._task_resident_memory = ""
        except Exception:
            self._close_owned_resources()
            if self._owns_mcp_client and effective_mcp_client is not None:
                try:
                    effective_mcp_client.close()
                except Exception:
                    logger.exception(
                        "Failed to close owned MCP client after assembly error"
                    )
            raise

    def _close_owned_resources(self) -> list[str]:
        failures: list[str] = []
        for resource in reversed(self._owned_resources):
            try:
                resource.close()
            except Exception as exc:
                failures.append(
                    f"{type(resource).__name__}: {type(exc).__name__}: {exc}"
                )
        self._owned_resources = ()
        return failures

    def _register_mcp_tools(self) -> None:
        evaluation = self.config.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        post_conditions = evaluation.get("post_conditions")
        post_conditions = post_conditions if isinstance(post_conditions, dict) else {}
        for definition in self.mcp_client.list_tools():
            name = definition["name"]
            self.registry.register(
                MCPTool(
                    name,
                    definition,
                    self.mcp_client,
                    post_condition=post_conditions.get(name),
                )
            )

    def _register_evaluator_tool(
        self,
        *,
        evaluator: EvaluatorProtocol | None,
        observation_provider: PostExecutionObservationProvider | None,
    ) -> None:
        """Compose the hidden evaluator only when post-execution hooks use it."""
        evaluation = self.config.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        required_tools = {
            str(name).strip()
            for name in evaluation.get("required_tools") or []
            if str(name).strip()
        }
        if not required_tools:
            return

        supplied_public_boundary = (
            evaluator is not None or observation_provider is not None
        )
        if self.registry.has(EVALUATE_RUN_TOOL_NAME):
            if supplied_public_boundary:
                raise ConfigurationError(
                    "The public evaluator boundary cannot be combined with a "
                    "pre-registered evaluate_run tool."
                )
            return
        if evaluator is None and observation_provider is None:
            return
        if evaluator is None or observation_provider is None:
            missing = (
                "evaluator"
                if evaluator is None
                else "post_execution_observation_provider"
            )
            raise ConfigurationError(
                f"Evaluation-required tools need the {missing} dependency."
            )
        self.registry.register(
            EvaluatorTool(
                registry=self.registry,
                evaluator=evaluator,
                observation_provider=observation_provider,
            )
        )

    def _begin_task_lifetime(self) -> None:
        """Acquire exclusive task ownership for this Harness instance."""
        if self._closed:
            raise RuntimeError("Cannot run a task after Harness.close().")
        if not self._task_lock.acquire(blocking=False):
            raise RuntimeError(
                "This Harness already has an active task. "
                "Use a separate Harness instance for concurrent tasks."
            )
        if self._closed:
            self._task_lock.release()
            raise RuntimeError("Cannot run a task after Harness.close().")
        self._task_active = True

    def _end_task_lifetime(self) -> None:
        """Release exclusive task ownership after cleanup."""
        if not self._task_active:
            return
        self._task_active = False
        self._task_lock.release()

    def reset_session(self) -> None:
        """Drop Accumulated context while retaining clients and tools."""
        if not self._task_lock.acquire(blocking=False):
            raise RuntimeError("Cannot reset the session while a task is active.")
        try:
            if self._closed:
                raise RuntimeError("Cannot reset the session after Harness.close().")
            self.ctx = None
            self._visible_tool_definitions = []
            self.skill_system.end_task()
            self._loaded_skill_context_metadata = []
            self._task_resident_memory = ""
            self.context_compactor.reset()
            self.memory.task_notes.expire()
        finally:
            self._task_lock.release()

    def _load_task_context(self) -> None:
        self._sync_execution_dependencies()
        self.hooks.validate_hook_configuration()
        definitions_without_experience = self.hooks.model_visible_definitions(
            self.registry.list_tool_definitions()
        )
        visible_names = {
            str(definition.get("name") or "")
            for definition in definitions_without_experience
            if str(definition.get("name") or "")
        }
        summaries = self.memory.tool_experience_summaries(visible_names)
        self._visible_tool_definitions = self.hooks.model_visible_definitions(
            self.registry.list_tool_definitions(summaries)
        )
        self._task_resident_memory = self.memory.resident_memory()
        if self.ctx is None:
            self.ctx = Context()
        self.ctx.set_tool_definitions(self._visible_tool_definitions)
        self.ctx.set_context_layers(
            resident=self._build_resident_context(),
            refreshed=self.ctx.refreshed_context,
        )

    def model_visible_tool_definitions(self) -> list[dict[str, Any]]:
        """Return the Tool Definitions exposed to model-facing interfaces."""
        if self._visible_tool_definitions:
            return deepcopy(self._visible_tool_definitions)
        return deepcopy(
            self.hooks.model_visible_definitions(self.registry.list_tool_definitions())
        )

    def _loaded_skill_context_blocks(self) -> list[tuple[str, str]]:
        projection = self.skill_system.context()
        self._loaded_skill_context_metadata = [
            dict(item) for item in projection.metadata
        ]
        return list(projection.blocks)

    def _build_resident_context(
        self,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        include_loaded_skill: bool = True,
    ) -> str:
        return build_resident_context(
            system_prompt=system_prompt,
            memory=self._task_resident_memory,
            embodiment_profile=self.embodiment_profile,
            skill_catalog=self.skill_catalog,
            task_context_blocks=(
                self._loaded_skill_context_blocks() if include_loaded_skill else None
            ),
        )

    def _refresh_context(
        self,
        system_prompt_override: str | None = None,
    ) -> None:
        if self.ctx is None:
            return
        system_prompt = (
            extract_system_prompt(system_prompt_override)
            if system_prompt_override is not None
            else SYSTEM_PROMPT
        )
        self.scene_graph.refresh_from_perception()
        refreshed = build_refreshed_context(
            scene_graph_brief=render_scene_graph_brief(self.scene_graph.brief()),
        )
        self.ctx.set_context_layers(
            resident=self._build_resident_context(
                system_prompt=system_prompt,
            ),
            refreshed=refreshed,
        )

    def _capture_observation(self, turn: int) -> dict[str, Any]:
        assert self.ctx is not None
        self.ctx.clear_observation_messages()
        return self.observation_provider(
            ObservationSink(self.ctx),
            turn,
        )

    def _refresh_safety_context(self, turn: int) -> dict[str, Any] | None:
        assert self.ctx is not None
        return self.hooks.refresh_decision_context(
            ObservationSink(self.ctx),
            turn=turn,
        )

    def _build_context(
        self,
        turn: int,
        system_prompt_override: str | None,
    ) -> list[dict[str, Any]]:
        assert self.ctx is not None
        task_notes = self.memory.task_notes.snapshot_message_if_changed()
        if task_notes is not None:
            self.ctx.add_accumulated_message(
                task_notes,
                kind=TASK_NOTES_MESSAGE_KIND,
                source="task_notes",
            )
        events = [self._capture_observation(turn)]
        safety_refresh = self._refresh_safety_context(turn)
        if safety_refresh is not None:
            events.append(safety_refresh)
        self._refresh_context(system_prompt_override)
        compaction_model = (
            self.model if self.compaction_model is None else self.compaction_model
        )
        compaction = self.context_compactor.maybe_compact(
            self.ctx,
            compaction_model,
        )
        if compaction is not None:
            events.append({**compaction, "turn": turn})
        return events

    def _prompt_snapshot(self, turn: int) -> dict[str, Any]:
        assert self.ctx is not None
        arguments: dict[str, Any] = {
            "turn": turn,
            "provider_system_content": self.ctx.provider_system_content,
            "tool_definitions": deepcopy(self._visible_tool_definitions),
            "accumulated_messages": deepcopy(self.ctx.accumulated_messages),
            "observation_messages": deepcopy(self.ctx.observation_messages),
            "loaded_skill_context_blocks": deepcopy(
                self._loaded_skill_context_metadata
            ),
            "resident_context": self.ctx.resident_context,
            "refreshed_context": self.ctx.refreshed_context,
            "transient_tool_result_messages": deepcopy(
                self.ctx.transient_tool_result_messages
            ),
        }
        return self.prompt_snapshot_builder(**arguments)

    def _sync_execution_dependencies(self) -> None:
        """Keep mutable deployment adapters on one Tool Registry boundary."""
        if self.hooks.registry is not self.registry:
            self.hooks.bind_registry(self.registry)

    def _execution_pipeline(self) -> ToolExecutionPipeline:
        """Bind one execution pipeline to the current integrations."""
        self._sync_execution_dependencies()
        return ToolExecutionPipeline(
            registry=self.registry,
            hooks=self.hooks,
            memory=self.memory,
            scene_graph=self.scene_graph,
            tool_result_effects=(self.skill_system,),
        )

    def _reject_extra_tool_calls(
        self,
        calls: list[ToolCall],
        turn: int,
    ) -> list[dict[str, Any]]:
        assert self.ctx is not None
        return self._execution_pipeline().reject_extra_calls(
            calls,
            turn=turn,
            context=self.ctx,
        )

    def _call_model(
        self,
        turn: int,
    ) -> tuple[ModelResponse, dict[str, Any]]:
        assert self.ctx is not None
        started = time.monotonic()
        try:
            response = self.model.call(
                self.ctx,
                self._visible_tool_definitions,
            )
        finally:
            self.ctx.clear_transient_tool_result_messages()
            self.ctx.clear_observation_messages()
        duration_ms = int((time.monotonic() - started) * 1000)
        self.ctx.add_model_response(response)
        return response, {
            "type": "model_response",
            "turn": turn,
            "text": response.text,
            "reasoning_content": response.reasoning_content,
            "tool_call_count": len(response.tool_calls),
            "say_event_emitted": bool(response.text and response.tool_calls),
            "stop_reason": str(getattr(response, "stop_reason", "") or ""),
            "duration_ms": duration_ms,
        }

    def _task_end_consolidation_event(
        self,
        *,
        turn: int,
        termination_reason: str,
        consolidate: bool,
    ) -> dict[str, Any]:
        allowed_tools = {
            str(definition.get("name") or "")
            for definition in self._visible_tool_definitions
            if str(definition.get("name") or "")
        }
        return consolidate_task_notes_at_task_end(
            memory=self.memory,
            allowed_tools=allowed_tools,
            turn=turn,
            consolidate=consolidate,
            skip_reason=termination_reason,
        )

    def _clear_task_scope(self) -> None:
        """Release Skill System state and clear task-scoped Context."""
        self.skill_system.end_task()
        self._loaded_skill_context_metadata = []
        clear_task_scope(
            memory=self.memory,
            context=self.ctx,
            resident_context=lambda: self._build_resident_context(
                include_loaded_skill=False,
            ),
        )

    @property
    def loaded_skill(self) -> dict[str, Any] | None:
        """Return the Skill System body loaded for the active task."""
        return self.skill_system.loaded_skill

    def close(self) -> None:
        """Close owned runtime resources when no task is active."""
        if self._closed:
            return
        if not self._task_lock.acquire(blocking=False):
            raise RuntimeError("Cannot close the Harness while a task is active.")
        try:
            if self._closed:
                return
            self._closed = True
            failures = self._close_owned_resources()
            if self._owns_mcp_client:
                try:
                    self.mcp_client.close()
                except Exception as exc:
                    failures.append(f"MCPClient: {type(exc).__name__}: {exc}")
            if failures:
                raise RuntimeError(
                    "Harness resource shutdown failures: " + "; ".join(failures)
                )
        finally:
            self._task_lock.release()


__all__ = ["Harness"]
