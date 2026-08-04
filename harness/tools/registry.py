"""Unified tool protocol for BuiltinTool and MCPTool.

Following the practice of coding agents:
- Lightweight operations such as lookups and user interaction run in-process
  as BuiltinTool instances.
- Heavy operations such as navigation and manipulation policies are exposed
  through MCPTool proxies backed by MCP capability subprocesses. One backend
  capability may expose several related Tool endpoints from its process.

The model sees one Tool Definition format and is independent of the execution
backend.
"""

from __future__ import annotations

import inspect
import logging
import types
from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Protocol,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    runtime_checkable,
)

from harness.tools.schema import (
    validate_arguments,
    validate_schema_definition,
)

if TYPE_CHECKING:
    from harness.protocols import MCPClientProtocol

logger = logging.getLogger(__name__)


@runtime_checkable
class Tool(Protocol):
    """Tool interface used by the harness execution pipeline."""

    @property
    def name(self) -> str: ...

    @property
    def schema(self) -> dict[str, Any]:
        """MCP-style Tool Definition: {name, description, inputSchema}."""
        ...

    def call(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool and return one Tool Result."""
        ...


class BuiltinTool:
    """In-process tool with no subprocess or MCP overhead.

    Intended for lightweight operations such as user interaction and read-only
    status queries.
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        fn: Callable[..., Any],
        *,
        post_condition: str | None = None,
    ):
        self._name = name
        self._description = description
        self._input_schema = input_schema
        self._fn = fn
        self._post_condition = _normalize_post_condition(post_condition)

    @classmethod
    def from_callable(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        post_condition: str | None = None,
    ) -> BuiltinTool:
        """Build a Tool Definition from a function signature and docstring.

        The inferred schema intentionally covers ordinary JSON-compatible
        annotations.  Deployments with richer constraints can pass an explicit
        ``input_schema`` while retaining the same registration path.
        """
        tool_name = str(name or getattr(fn, "__name__", "")).strip()
        tool_description = str(
            description if description is not None else inspect.getdoc(fn) or ""
        ).strip()
        schema = (
            dict(input_schema)
            if input_schema is not None
            else _input_schema_from_callable(fn)
        )
        declared_post_condition = (
            post_condition
            if post_condition is not None
            else _callable_post_condition(fn)
        )
        return cls(
            name=tool_name,
            description=tool_description,
            input_schema=schema,
            fn=fn,
            post_condition=declared_post_condition,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "description": self._description,
            "inputSchema": self._input_schema,
        }

    @property
    def post_condition(self) -> str | None:
        """Return the evaluator criterion kept outside model-visible schema."""
        return self._post_condition

    def call(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _normalize_tool_result(self._fn(**args))
        except Exception as exc:
            logger.exception("Builtin tool %s raised an exception", self._name)
            return {
                "success": False,
                "reason": f"Tool execution failed ({type(exc).__name__}).",
            }


class MCPTool:
    """Proxy for a tool hosted by an MCP capability subprocess.

    Navigation (SLAM) and manipulation (ACT/VLA) are typical operations that
    benefit from separate process boundaries. One capability process may host
    several related Tool endpoints.
    """

    def __init__(
        self,
        name: str,
        schema: dict[str, Any],
        client: MCPClientProtocol,
        *,
        post_condition: str | None = None,
    ):
        self._name = name
        self._schema = {
            "name": schema.get("name"),
            "description": schema.get("description"),
            "inputSchema": schema.get("inputSchema"),
        }
        self._client = client
        self._post_condition = _normalize_post_condition(post_condition)

    @property
    def name(self) -> str:
        return self._name

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    @property
    def post_condition(self) -> str | None:
        """Return the evaluator criterion configured for this MCP tool."""
        return self._post_condition

    def call(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._client.call_tool(self._name, args)


class ToolRegistry:
    """Register one flat model-facing list across both execution backends."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        _validate_tool_definition(tool)
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        self._tools[tool.name] = tool

    def tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        post_condition: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a builtin callable while leaving the function reusable."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                BuiltinTool.from_callable(
                    fn,
                    name=name,
                    description=description,
                    input_schema=input_schema,
                    post_condition=post_condition,
                )
            )
            return fn

        return decorator

    def replace(self, tool: Tool) -> None:
        """Replace an existing channel adapter without weakening registration.

        Deployment channels use this explicit operation to replace fallback
        user-interaction tools. Accidental duplicate registration remains an
        error.
        """
        _validate_tool_definition(tool)
        if tool.name not in self._tools:
            raise KeyError(f"cannot replace unregistered tool: {tool.name!r}")
        self._tools[tool.name] = tool

    def list_tool_definitions(
        self,
        tool_experience_summaries: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return Tool Definitions with experience appended by tool identity."""
        summaries = tool_experience_summaries or {}
        definitions: list[dict[str, Any]] = []
        for tool in self._tools.values():
            definition = {
                "name": tool.schema["name"],
                "description": tool.schema["description"],
                "inputSchema": tool.schema["inputSchema"],
            }
            summary = str(summaries.get(tool.name) or "").strip()
            if summary:
                description = str(definition.get("description") or "").rstrip()
                definition["description"] = (
                    description + "\n\nTool Experience:\n" + summary
                )
            definitions.append(definition)
        return definitions

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        validation_failure = self.validate_call(name, arguments)
        if validation_failure is not None:
            return validation_failure
        tool = self._tools[name]
        try:
            return _normalize_tool_result(tool.call(arguments))
        except Exception as exc:
            logger.exception("Tool %s raised an exception", name)
            return {
                "success": False,
                "reason": f"Tool execution failed ({type(exc).__name__}).",
            }

    def validate_call(
        self,
        name: str,
        arguments: Any,
    ) -> dict[str, Any] | None:
        """Validate a proposed call without executing the tool.

        ``None`` means the call is ready to enter pre-execution hooks. A
        failure uses the same model-visible envelope returned by ``call_tool``.
        """
        tool = self._tools.get(name)
        if tool is None:
            return {"success": False, "reason": f"Unknown tool: {name}"}
        validation_error = validate_arguments(
            tool.schema.get("inputSchema", {}),
            arguments,
        )
        if not validation_error:
            return None
        return {
            "success": False,
            "reason": f"Invalid tool arguments: {validation_error}",
            "kind": "invalid_tool_arguments",
        }

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools.keys())

    def post_condition(self, name: str) -> str | None:
        """Return one tool's evaluator post-condition, when declared."""
        tool = self._tools.get(name)
        if tool is None:
            return None
        return _normalize_post_condition(getattr(tool, "post_condition", None))

    def post_conditions(self) -> dict[str, str]:
        """Return all declared post-conditions keyed by tool identity."""
        return {
            name: post_condition
            for name in self._tools
            if (post_condition := self.post_condition(name)) is not None
        }


def _normalize_tool_result(result: Any) -> dict:
    """Enforce the model-visible tool result envelope at the registry boundary."""
    if not isinstance(result, dict):
        return _tool_protocol_failure(
            f"expected dict result, got {type(result).__name__}"
        )

    normalized = dict(result)
    if "success" not in normalized:
        return _tool_protocol_failure("missing required boolean field 'success'")
    if not isinstance(normalized["success"], bool):
        return _tool_protocol_failure("field 'success' must be a boolean")

    if normalized["success"]:
        value_keys = set(normalized) - {"success", "reason", "error"}
        if not value_keys:
            return _tool_protocol_failure(
                "successful result must include at least one value field"
            )
        normalized.pop("error", None)
        return normalized

    reason = normalized.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return _tool_protocol_failure(
            "failed result must include a non-empty string field 'reason'"
        )
    normalized["reason"] = reason.strip()
    normalized.pop("error", None)
    return normalized


def _tool_protocol_failure(detail: str) -> dict[str, Any]:
    return {
        "success": False,
        "reason": f"Tool protocol violation: {detail}.",
        "kind": "tool_protocol_violation",
    }


def _normalize_post_condition(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _callable_post_condition(fn: Callable[..., Any]) -> str | None:
    """Read a tool file's hidden ``POST_CONDITION`` when it is declared."""
    direct = getattr(fn, "POST_CONDITION", None)
    if direct is not None:
        return _normalize_post_condition(direct)
    namespace = getattr(fn, "__globals__", {})
    if isinstance(namespace, dict):
        return _normalize_post_condition(namespace.get("POST_CONDITION"))
    return None


def _input_schema_from_callable(fn: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except (NameError, TypeError):
        hints = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        }:
            raise TypeError(
                "BuiltinTool.from_callable supports named parameters only; "
                f"{fn.__name__}.{parameter.name} uses {parameter.kind.description}."
            )
        annotation = hints.get(parameter.name, parameter.annotation)
        properties[parameter.name] = _json_schema_for_annotation(annotation)
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
        else:
            properties[parameter.name]["default"] = parameter.default
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _json_schema_for_annotation(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        schema = _json_schema_for_annotation(args[0] if args else Any)
        description = _annotated_description(args[1:])
        if description:
            schema["description"] = description
        return schema
    if origin in {Union, types.UnionType}:
        variants = [
            {"type": "null"}
            if item is type(None)
            else _json_schema_for_annotation(item)
            for item in args
        ]
        return {"anyOf": variants}
    if origin in {list, tuple, set, frozenset}:
        item_annotation = args[0] if args else Any
        return {
            "type": "array",
            "items": _json_schema_for_annotation(item_annotation),
        }
    if origin is dict:
        return {"type": "object"}
    return {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }.get(annotation, {})


def _annotated_description(metadata: tuple[Any, ...]) -> str:
    """Extract a Field-style description without depending on Pydantic."""
    for item in metadata:
        description = getattr(item, "description", None)
        if isinstance(description, str) and description.strip():
            return description.strip()
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _validate_tool_definition(tool: Tool) -> None:
    """Fail at registration when a tool definition violates the protocol."""
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool name must be a non-empty string")

    schema = getattr(tool, "schema", None)
    if not isinstance(schema, dict):
        raise ValueError(f"tool {name!r} schema must be a mapping")
    required_fields = {"name", "description", "inputSchema"}
    missing = sorted(required_fields - set(schema))
    if missing:
        raise ValueError(
            f"tool {name!r} schema is missing required fields: " + ", ".join(missing)
        )
    unexpected = sorted(set(schema) - required_fields)
    if unexpected:
        raise ValueError(
            f"tool {name!r} schema contains fields outside the Tool Definition: "
            + ", ".join(unexpected)
        )
    if schema.get("name") != name:
        raise ValueError(
            f"tool name {name!r} does not match schema name {schema.get('name')!r}"
        )
    description = schema.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"tool {name!r} schema description must be a non-empty string")
    if not isinstance(schema.get("inputSchema"), dict):
        raise ValueError(f"tool {name!r} inputSchema must be a mapping")
    schema_error = validate_schema_definition(schema["inputSchema"])
    if schema_error:
        raise ValueError(f"tool {name!r} has invalid inputSchema: {schema_error}")
