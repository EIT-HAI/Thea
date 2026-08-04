"""
Blocking client over the official Model Context Protocol Python SDK.

Each MCPClient owns one or more _SessionRunner instances, one per configured
backend capability process.
Sync↔async bridging is handled by anyio blocking portal; see _SessionRunner
for why session lifecycle stays inside a single async task.

"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import os
import queue
import threading
from pathlib import Path
from typing import Any

from anyio.from_thread import BlockingPortal, start_blocking_portal

logger = logging.getLogger(__name__)


class MCPError(RuntimeError):
    """MCP transport or lifecycle failure."""


class MCPExecutionStatusUnknown(MCPError):
    """A timed-out call may still be executing in the physical world."""


# Internal runner protocol operations.
_OP_LIST_TOOLS = "list_tools"
_OP_CALL_TOOL = "call_tool"
_OP_SHUTDOWN = "shutdown"


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _call_tool_timeout_sec(
    arguments: dict | None,
    *,
    default_timeout: float,
    argument_margin: float,
) -> float:
    """Resolve a transport timeout without knowing tool names."""
    requested_timeout = 0.0
    if isinstance(arguments, dict) and arguments.get("timeout_sec") is not None:
        try:
            requested_timeout = float(arguments.get("timeout_sec"))
        except (TypeError, ValueError):
            requested_timeout = 0.0
    if not math.isfinite(requested_timeout) or requested_timeout <= 0:
        return default_timeout
    return max(default_timeout, requested_timeout + argument_margin)


def _validate_server_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise ValueError("MCP server config must be a dictionary.")
    command = config.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("MCP server command must be a non-empty string.")
    args = config.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("MCP server args must be a list of strings.")
    env = config.get("env")
    if env is not None and not isinstance(env, dict):
        raise ValueError("MCP server env must be a dictionary.")
    cwd = config.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
        raise ValueError("MCP server cwd must be a non-empty string path.")
    name = config.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError("MCP server name must be a non-empty string.")
    _validate_timeout_config(
        config.get("call_timeout_sec"),
        field_name="call_timeout_sec",
        allow_zero=False,
    )
    _validate_timeout_config(
        config.get("timeout_argument_margin_sec"),
        field_name="timeout_argument_margin_sec",
        allow_zero=True,
    )


def _validate_timeout_config(
    value: Any,
    *,
    field_name: str,
    allow_zero: bool,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"MCP server {field_name} must be a finite number.")
    number = float(value)
    valid = number >= 0 if allow_zero else number > 0
    if not math.isfinite(number) or not valid:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(
            f"MCP server {field_name} must be a finite, {qualifier} number."
        )


class MCPClient:
    """Synchronous MCP client wrapper.

    Connects to every configured MCP capability process at startup, collects
    its Tool Definitions, and routes calls to the owning process.

    Each server configuration is a dictionary:
        {"name": "deployment-tools",
         "command": "python",
         "args": ["-m", "my_deployment.mcp_server"],
         "transport": "stdio"}

    Server configuration is a trusted code-execution boundary: ``command`` is
    launched directly as a subprocess. Never build it from model output or
    untrusted remote input. Server executables and modules are supplied by the
    deployment; the harness package does not bundle an MCP tool server.
    """

    def __init__(self, server_configs: list[dict]):
        self._server_configs = list(server_configs)
        self._sessions: dict[str, _SessionRunner] = {}  # name → runner
        self._tools: dict[str, dict[str, Any]] = {}
        self._execution_status_unknown = ""
        try:
            for cfg in self._server_configs:
                _validate_server_config(cfg)
                self._connect_server(cfg)
        except Exception:
            for runner in self._sessions.values():
                try:
                    runner.close()
                except Exception:
                    logger.exception(
                        "%s: cleanup failed after MCP initialization error",
                        runner.name,
                    )
            self._sessions.clear()
            self._tools.clear()
            raise

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def _connect_server(self, config: dict) -> None:
        """Connect one MCP server and load its tools.

        Each server gets one stdio subprocess and one _SessionRunner whose
        background event loop owns the initialized session.
        """
        name = config.get("name") or "unnamed"
        if name in self._sessions:
            raise ValueError(f"duplicate server name: {name!r}")

        runner = _SessionRunner(
            name=name,
            command=config["command"],
            args=config.get("args", []),
            env=config.get("env"),
            cwd=config.get("cwd"),
        )
        committed = False
        try:
            runner.start()  # blocks until session.initialize() completes
            tools = runner.list_tools()
            seen: set[str] = set()
            duplicates: set[str] = set()
            for tool in tools:
                tool_name = tool["name"]
                if tool_name in seen or tool_name in self._tools:
                    duplicates.add(tool_name)
                seen.add(tool_name)
            if duplicates:
                raise ValueError(
                    f"MCP server {name!r} exposes duplicate tool names: "
                    + ", ".join(sorted(duplicates)),
                )

            default_timeout_value = config.get("call_timeout_sec")
            default_timeout = float(
                _env_float("THEA_MCP_CALL_TIMEOUT_SEC", 300.0)
                if default_timeout_value is None
                else default_timeout_value
            )
            if default_timeout <= 0:
                default_timeout = 300.0
            argument_margin_value = config.get("timeout_argument_margin_sec")
            argument_margin = float(
                _env_float("THEA_MCP_TIMEOUT_ARGUMENT_MARGIN_SEC", 60.0)
                if argument_margin_value is None
                else argument_margin_value
            )
            if argument_margin < 0:
                argument_margin = 60.0
            self._sessions[name] = runner
            for tool in tools:
                self._tools[tool["name"]] = {
                    "definition": tool,
                    "server": name,
                    "default_timeout": default_timeout,
                    "argument_margin": argument_margin,
                }
            committed = True
        finally:
            if not committed:
                try:
                    runner.close()
                except Exception:
                    logger.exception(
                        "%s: cleanup failed before MCP server registration",
                        name,
                    )

    def list_tools(self) -> list[dict]:
        """Return Tool Definitions from all connected MCP servers."""
        return [binding["definition"] for binding in self._tools.values()]

    def call_tool(
        self,
        name: str,
        arguments: dict,
        *,
        timeout: float | None = None,
    ) -> dict:
        """Call a tool on its owning MCP server.

        Transport and MCP errors are normalized into a dictionary containing
        ``success=false``.
        """
        if name not in self._tools:
            return {"success": False, "reason": f"Unknown tool: {name}"}
        if self._execution_status_unknown:
            return {
                "success": False,
                "kind": "mcp_execution_status_unknown",
                "reason": (
                    "MCP execution is blocked because a timed-out call on "
                    f"server {self._execution_status_unknown!r} may still be "
                    "running. Restart the MCP client after confirming the "
                    "robot is stopped."
                ),
            }

        binding = self._tools[name]
        server_name = binding["server"]
        runner = self._sessions[server_name]
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            return {
                "success": False,
                "kind": "invalid_mcp_timeout",
                "reason": "MCP call timeout must be a finite, positive number.",
            }
        resolved_timeout = (
            float(timeout)
            if timeout is not None
            else _call_tool_timeout_sec(
                arguments,
                default_timeout=float(binding["default_timeout"]),
                argument_margin=float(binding["argument_margin"]),
            )
        )
        result = runner.call_tool(name, arguments, timeout=resolved_timeout)
        if result.get("kind") == "mcp_execution_status_unknown":
            self._execution_status_unknown = server_name
        return result

    def close(self) -> None:
        """Close every server subprocess."""
        failures: list[str] = []
        for runner in self._sessions.values():
            try:
                runner.close()
            except Exception as exc:
                failures.append(f"{runner.name}: {type(exc).__name__}: {exc}")
        self._sessions.clear()
        self._tools.clear()
        self._execution_status_unknown = ""
        if failures:
            raise MCPError("MCP shutdown failures: " + "; ".join(failures))

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ============================================================
# Internal session runner for one server (sync-to-async bridge).
# ============================================================


class _SessionRunner:
    """Sync front to one MCP server.

    A background anyio event loop holds the entire `stdio_client` +
    `ClientSession` lifecycle inside one long-running task. The main thread
    submits commands via a thread-safe queue and reads results via Futures.
    Keeping connect/use/close in one task avoids anyio TaskGroup
    cross-task cancel-scope errors.
    """

    def __init__(
        self,
        *,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, Any] | None = None,
        cwd: str | Path | None = None,
    ):
        self.name = name
        self.command = command
        self.args = args
        self.env = (
            {**os.environ, **{str(k): str(v) for k, v in env.items()}}
            if env is not None
            else dict(os.environ)
        )
        self.cwd = cwd
        self._portal_cm = None
        self._portal: BlockingPortal | None = None
        self._cmd_queue: queue.Queue | None = None
        self._connect_done = threading.Event()
        self._connect_exc: list[BaseException] = []
        self._runner_future: concurrent.futures.Future | None = None
        self._closed = False

    def start(self) -> None:
        if self._portal is not None:
            raise RuntimeError(f"{self.name}: already started")
        self._cmd_queue = queue.Queue()
        self._portal_cm = start_blocking_portal(backend="asyncio")
        self._portal = self._portal_cm.__enter__()
        self._runner_future = self._portal.start_task_soon(self._async_runner)

        ok = self._connect_done.wait(timeout=15)
        if not ok:
            self._teardown()
            raise MCPError(f"{self.name}: server failed to initialize within 15s")
        if self._connect_exc:
            self._teardown()
            exc = self._connect_exc[0]
            raise MCPError(
                f"{self.name}: connect failed: {type(exc).__name__}: {exc}",
            ) from exc

    async def _async_runner(self) -> None:
        # Lazy-import so callers without mcp installed can still import this module.
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters

        try:
            params = StdioServerParameters(
                command=self.command,
                args=list(self.args),
                env=self.env,
                cwd=self.cwd,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._connect_done.set()

                    while True:
                        cmd = await asyncio.to_thread(self._cmd_queue.get)
                        op, args, fut = cmd
                        if op == _OP_SHUTDOWN:
                            fut.set_result(None)
                            return
                        try:
                            if op == _OP_LIST_TOOLS:
                                r = await session.list_tools()
                            elif op == _OP_CALL_TOOL:
                                tool_name, tool_args = args
                                r = await session.call_tool(tool_name, tool_args)
                            else:
                                raise ValueError(f"unknown op: {op}")
                            fut.set_result(r)
                        except Exception as exc:
                            fut.set_exception(exc)
        except BaseException as exc:
            self._connect_exc.append(exc)
            self._connect_done.set()
            raise

    def _send_cmd(self, op: str, args: Any = None, *, timeout: float = 300.0) -> Any:
        if self._cmd_queue is None:
            raise MCPError(f"{self.name}: not connected")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._cmd_queue.put((op, args, fut))
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise MCPExecutionStatusUnknown(
                f"{self.name}: {op} timed out after {timeout}s",
            ) from exc

    def list_tools(self) -> list[dict]:
        r = self._send_cmd(_OP_LIST_TOOLS)
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema,
            }
            for t in r.tools
        ]

    def call_tool(
        self,
        name: str,
        arguments: dict,
        *,
        timeout: float = 300.0,
    ) -> dict:
        try:
            tool_args = arguments or {}
            r = self._send_cmd(
                _OP_CALL_TOOL,
                (name, tool_args),
                timeout=timeout,
            )
            return _parse_call_tool_result(r)
        except MCPExecutionStatusUnknown as exc:
            return {
                "success": False,
                "kind": "mcp_execution_status_unknown",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        except Exception as exc:
            return {
                "success": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    def close(self) -> None:
        if self._portal is None or self._closed:
            return
        failures: list[BaseException] = []
        try:
            try:
                self._send_cmd(_OP_SHUTDOWN, timeout=3.0)
            except Exception as exc:
                failures.append(exc)
            if self._runner_future:
                try:
                    self._runner_future.result(timeout=5.0)
                except Exception as exc:
                    failures.append(exc)
        finally:
            self._teardown()
            self._closed = True
        if failures:
            details = "; ".join(f"{type(exc).__name__}: {exc}" for exc in failures)
            raise MCPError(f"{self.name}: shutdown failed: {details}")

    def _teardown(self) -> None:
        if self._portal_cm is not None:
            try:
                self._portal_cm.__exit__(None, None, None)
            except Exception:
                logger.debug(
                    "%s: portal teardown raised during best-effort cleanup",
                    self.name,
                    exc_info=True,
                )
        self._portal_cm = None
        self._portal = None
        self._cmd_queue = None


# ============================================================
# Parse an SDK CallToolResult into the harness result dictionary.
# ============================================================


def _parse_call_tool_result(r: Any) -> dict:
    """Decode a dictionary serialized by FastMCP as JSON text.

    Prefer structuredContent when available and otherwise decode
    ``content[0].text``.
    """
    is_error = bool(getattr(r, "isError", False))
    struct = getattr(r, "structuredContent", None)
    if isinstance(struct, dict) and struct:
        if "result" in struct and isinstance(struct["result"], dict):
            decoded = struct["result"]
        else:
            decoded = struct
        return _normalize_mcp_server_result(decoded, is_error=is_error)

    content = getattr(r, "content", None) or []
    if not content:
        decoded = {"success": False, "reason": "empty content from server"}
        return _normalize_mcp_server_result(decoded, is_error=is_error)

    first = content[0]
    text = getattr(first, "text", None)
    if text is None:
        decoded = {
            "success": False,
            "reason": f"non-text content: {type(first).__name__}",
        }
        return _normalize_mcp_server_result(decoded, is_error=is_error)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        decoded = {"success": False, "reason": "non-json content", "raw": text}
        return _normalize_mcp_server_result(decoded, is_error=is_error)

    if not isinstance(payload, dict):
        decoded = {
            "success": False,
            "reason": "tool result is not a dict",
            "raw": payload,
        }
        return _normalize_mcp_server_result(decoded, is_error=is_error)
    return _normalize_mcp_server_result(payload, is_error=is_error)


def _normalize_mcp_server_result(
    result: dict[str, Any],
    *,
    is_error: bool,
) -> dict[str, Any]:
    """Preserve decoded fields while honoring the MCP transport error flag."""
    normalized = dict(result)
    if not is_error:
        return normalized
    reason = str(
        normalized.get("reason")
        or normalized.get("message")
        or "MCP server reported a tool execution error."
    ).strip()
    normalized.update(
        {
            "success": False,
            "kind": str(normalized.get("kind") or "mcp_tool_error"),
            "reason": reason,
        }
    )
    return normalized
