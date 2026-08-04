"""Public API for the provider-neutral Thea Harness."""

from harness.configuration.runtime import ConfigurationError, validate_runtime_config
from harness.context import Context, ModelResponse, ObservationSink, ToolCall
from harness.context.compaction import CompactionConfig, ContextCompactor
from harness.evaluation.contract import (
    EvaluationRequest,
    EvaluatorProtocol,
    EvaluatorStatus,
    EvaluatorTool,
    EvaluatorVerdict,
    PostExecutionObservationProvider,
)
from harness.evaluation.model import (
    EvaluatorResponseError,
    ModelEvaluator,
)
from harness.memory.store import (
    DurableMemoryEntry,
    FileMemory,
    MemoryConsolidation,
    MemoryConsolidatorProtocol,
    MemorySection,
    ModelMemoryConsolidator,
    ModelToolExperienceSummarizer,
    TaskNotes,
    TaskNotesSnapshot,
    ToolExperienceEntry,
    ToolExperienceOutcome,
    ToolExperienceSummarizerProtocol,
)
from harness.protocols import (
    MCPClientProtocol,
    MemoryProtocol,
    ModelProtocol,
    ObservationProvider,
    PromptSnapshotBuilder,
    RuntimeResource,
    SceneGraphProtocol,
    TaskNotesProtocol,
    ToolRegistrar,
)
from harness.runtime.core import Harness
from harness.runtime.execution import ToolExecution
from harness.runtime.hooks import ToolHooks
from harness.runtime.standalone import InMemoryTaskNotes, NullMemory, NullSceneGraph
from harness.tools.registry import BuiltinTool, MCPTool, Tool, ToolRegistry
from harness.world.embodiment import (
    EMBODIMENT_PROFILE_ITEMS,
    EmbodimentProfile,
    embodiment_profile_path_from_config,
    load_embodiment_profile_document_from_config,
)
from harness.world.observation import (
    BaseClearance,
    Observation,
    VisualEvidence,
    publish_observation,
    render_observation_messages,
)
from harness.world.safety import (
    BaseClearanceProvider,
    BaseMotionSafetyFilter,
    PreExecutionHook,
    PreExecutionHookResult,
)
from harness.world.scene_graph import (
    SCENE_GRAPH_RELATION_TYPES,
    SceneGraphBrief,
    SceneGraphObjectBrief,
    SceneGraphQueryProtocol,
    SceneGraphRelation,
    SceneGraphRobotBrief,
    register_scene_graph_query_tools,
    render_scene_graph_brief,
)

__version__ = "0.1.0"

__all__ = [
    "BuiltinTool",
    "BaseClearance",
    "BaseClearanceProvider",
    "BaseMotionSafetyFilter",
    "CompactionConfig",
    "ConfigurationError",
    "Context",
    "ContextCompactor",
    "DurableMemoryEntry",
    "EMBODIMENT_PROFILE_ITEMS",
    "EmbodimentProfile",
    "EvaluationRequest",
    "EvaluatorProtocol",
    "EvaluatorResponseError",
    "EvaluatorStatus",
    "EvaluatorTool",
    "EvaluatorVerdict",
    "FileMemory",
    "Harness",
    "InMemoryTaskNotes",
    "MCPClientProtocol",
    "MCPTool",
    "MemoryConsolidation",
    "MemoryConsolidatorProtocol",
    "MemoryProtocol",
    "MemorySection",
    "ModelProtocol",
    "ModelResponse",
    "ModelEvaluator",
    "ModelMemoryConsolidator",
    "ModelToolExperienceSummarizer",
    "NullMemory",
    "NullSceneGraph",
    "Observation",
    "ObservationProvider",
    "ObservationSink",
    "PostExecutionObservationProvider",
    "PreExecutionHook",
    "PreExecutionHookResult",
    "PromptSnapshotBuilder",
    "RuntimeResource",
    "SCENE_GRAPH_RELATION_TYPES",
    "SceneGraphBrief",
    "SceneGraphObjectBrief",
    "SceneGraphProtocol",
    "SceneGraphQueryProtocol",
    "SceneGraphRelation",
    "SceneGraphRobotBrief",
    "TaskNotes",
    "TaskNotesProtocol",
    "TaskNotesSnapshot",
    "Tool",
    "ToolCall",
    "ToolExperienceEntry",
    "ToolExperienceOutcome",
    "ToolExperienceSummarizerProtocol",
    "ToolExecution",
    "ToolHooks",
    "ToolRegistrar",
    "ToolRegistry",
    "VisualEvidence",
    "embodiment_profile_path_from_config",
    "load_embodiment_profile_document_from_config",
    "publish_observation",
    "register_scene_graph_query_tools",
    "render_observation_messages",
    "render_scene_graph_brief",
    "validate_runtime_config",
]
