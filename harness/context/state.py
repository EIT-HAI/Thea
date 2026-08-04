"""Provider-neutral conversation context."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, Any]
TASK_NOTES_MESSAGE_KIND = "task_notes"


@dataclass
class ToolCall:
    """One provider-neutral request to execute a named tool."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    """One normalized model decision and its optional selected Tool Call."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""
    stop_reason: str = ""


class ObservationSink:
    """Restricted context surface available to an Observation provider."""

    def __init__(self, context: Context) -> None:
        self._context = context

    def set_observation_messages(self, messages: list[Message]) -> None:
        self._context.set_observation_messages(messages)

    def add_observation_messages(self, messages: list[Message]) -> None:
        self._context.add_observation_messages(messages)

    def clear_observation_messages(self) -> None:
        self._context.clear_observation_messages()


class Context:
    """
    Context is partitioned by the three lifetimes defined in the paper.

    ``resident_context`` contains the System Prompt, Memory, Embodiment
    Profile, and resident Skill System content. Tool Definitions belong to the
    same lifetime but travel through the provider's ``tools`` field.
    ``refreshed_context`` contains the latest Scene Graph Brief.
    ``observation_messages`` carries the latest Observation.
    ``transient_tool_result_messages`` transports image payloads associated
    with the latest Tool Result without creating a fourth context lifetime.
    ``accumulated_messages`` contains Instructions, Task Notes, Model Responses,
    and compact Tool Results. Compaction replaces only an older prefix of the
    accumulated messages.
    """

    def __init__(self):
        self.provider_system_content: str = ""
        self.resident_context: str = ""
        self.refreshed_context: str = ""
        self.tool_definitions: list[dict[str, Any]] = []
        self.accumulated_messages: list[Message] = []
        self.transient_tool_result_messages: list[Message] = []
        self.observation_messages: list[Message] = []

    def set_context_layers(self, *, resident: str, refreshed: str = "") -> None:
        """Set text context by lifetime and materialize the provider field."""
        self.resident_context = str(resident or "").strip()
        self.refreshed_context = str(refreshed or "").strip()
        self._materialize_provider_system_content()

    def _materialize_provider_system_content(self) -> None:
        self.provider_system_content = "\n\n".join(
            part for part in (self.resident_context, self.refreshed_context) if part
        )

    def set_tool_definitions(
        self,
        definitions: list[dict[str, Any]],
    ) -> None:
        """Set the Tool Definitions supplied with the next model call."""
        self.tool_definitions = list(definitions)

    def set_observation_messages(self, messages: list[Message]) -> None:
        self.observation_messages = list(messages)

    def add_observation_messages(self, messages: list[Message]) -> None:
        """Append evidence to the current Refreshed Observation."""
        self.observation_messages.extend(messages)

    def clear_observation_messages(self) -> None:
        self.observation_messages = []

    def add_transient_tool_result_message(self, message: Message) -> None:
        """Add a Tool Result image payload visible for one decision."""
        self.transient_tool_result_messages.append(dict(message))

    def clear_transient_tool_result_messages(self) -> None:
        """Remove image payloads that must not enter Accumulated context."""
        self.transient_tool_result_messages = []

    def messages_for_model(self) -> list[Message]:
        return (
            list(self.accumulated_messages)
            + list(self.transient_tool_result_messages)
            + list(self.observation_messages)
        )

    def add_accumulated_message(
        self,
        message: Message,
        *,
        kind: str = "",
        source: str = "",
    ) -> None:
        """Append one accumulated message with optional stable provenance."""
        tagged = dict(message)
        if kind:
            tagged["kind"] = str(kind)
        if source:
            tagged["source"] = str(source)
        self.accumulated_messages.append(tagged)

    def remove_accumulated_messages_by_kind(self, kind: str) -> int:
        """Remove accumulated messages carrying the requested stable kind."""
        target = str(kind or "").strip()
        if not target:
            return 0
        retained = [
            message
            for message in self.accumulated_messages
            if _message_metadata_value(message, "kind") != target
        ]
        removed = len(self.accumulated_messages) - len(retained)
        self.accumulated_messages = retained
        return removed

    def set_accumulated_message_by_kind(
        self,
        message: Message,
        *,
        kind: str,
        source: str = "",
    ) -> None:
        """Replace one accumulated message kind when singleton state needs it.

        Task Notes do not use this helper. Each changed Task Notes snapshot is
        appended to the active task's Accumulated context, matching the paper's
        context assembly, and all such snapshots expire together at task end.
        """
        target = str(kind or "").strip()
        if not target:
            raise ValueError("kind must be a non-empty string")
        self.remove_accumulated_messages_by_kind(target)
        self.add_accumulated_message(
            message,
            kind=target,
            source=source,
        )

    def add_user_message(self, content: Any) -> None:
        """Add plain text or multimodal content blocks.

        Multimodal blocks use the Anthropic-style form
        ``[{"type": "text", "text": "..."}, {"type": "image", ...}]``.
        Provider adapters convert this form when necessary.
        """
        self.accumulated_messages.append({"role": "user", "content": content})

    def add_model_response(self, response: ModelResponse) -> None:
        """Append one Model Response using the provider's assistant role.

        The provider-neutral message contains text plus optional Tool Calls. It
        must precede their Tool Results so provider APIs can resolve each result
        to the corresponding call.
        """
        msg: dict = {"role": "assistant", "content": response.text or None}
        reasoning_content = getattr(response, "reasoning_content", "") or ""
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ]
        self.accumulated_messages.append(msg)

    def add_tool_result(self, tool_call_id: str, result: dict[str, Any]) -> None:
        self.accumulated_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            }
        )

    def replace_accumulated_messages(self, messages: list[Message]) -> None:
        """Replace only accumulated history; resident/refreshed context is untouched."""
        self.accumulated_messages = list(messages)

    def estimate_token_count(self) -> int:
        """Estimate tokens from serialized character count."""
        total_chars = len(self.provider_system_content)
        total_chars += len(
            json.dumps(
                self.tool_definitions,
                ensure_ascii=False,
                default=str,
            )
        )
        total_chars += len(
            json.dumps(
                self.messages_for_model(),
                ensure_ascii=False,
                default=str,
            )
        )
        return total_chars // 4


def _message_metadata_value(message: Message, key: str) -> str:
    value = message.get(key)
    if value in (None, ""):
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get(key)
    return str(value or "").strip()


__all__ = [
    "Context",
    "Message",
    "ModelResponse",
    "ObservationSink",
    "TASK_NOTES_MESSAGE_KIND",
    "ToolCall",
]
