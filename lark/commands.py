"""Lark menu, admin, Skill, help, and Tool command routing."""

from __future__ import annotations

import asyncio
import json
import shlex
import textwrap
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .adapter import ParsedMessage
from .menu import TopMenuState, render_top_menu

SendText = Callable[[ParsedMessage, str], Awaitable[None]]
RunModelTask = Callable[..., Awaitable[None]]
FetchImages = Callable[[ParsedMessage], Awaitable[list[bytes]]]
CloseSession = Callable[..., Awaitable[None]]
ReturnToMenu = Callable[[Any, ParsedMessage], Awaitable[None]]


def parse_slash_args(rest: str) -> dict[str, Any]:
    """Parse ``key=value`` command arguments and coerce scalar values."""
    if not rest.strip():
        return {}
    result: dict[str, Any] = {}
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    for token in tokens:
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        if value.lower() == "true":
            result[key] = True
        elif value.lower() == "false":
            result[key] = False
        elif value.lstrip("-").isdigit():
            result[key] = int(value)
        else:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def format_tool_usage(schema: dict[str, Any]) -> str:
    """Render one-line slash-command usage for a Tool Definition."""
    name = schema.get("name", "?")
    input_schema = schema.get("inputSchema") or {}
    properties = input_schema.get("properties", {}) or {}
    required = set(input_schema.get("required", []) or [])
    parts: list[str] = [f"/{name}"]
    for property_name, property_schema in properties.items():
        property_type = property_schema.get("type", "string")
        token = f"{property_name}=<{property_type}>"
        parts.append(token if property_name in required else f"[{token}]")
    return " ".join(parts)


def format_tool_help(schema: dict[str, Any]) -> str:
    """Render detailed slash-command help for a Tool Definition."""
    description = (schema.get("description") or "").strip()
    input_schema = schema.get("inputSchema") or {}
    properties = input_schema.get("properties", {}) or {}
    required = set(input_schema.get("required", []) or [])

    lines = [format_tool_usage(schema), ""]
    if description:
        lines.append(textwrap.shorten(description, width=600, placeholder="..."))
        lines.append("")
    if not properties:
        lines.append("(No parameters)")
        return "\n".join(lines)

    lines.append("Parameters:")
    for property_name, property_schema in properties.items():
        property_type = property_schema.get("type", "string")
        requirement = "required" if property_name in required else "optional"
        property_description = (property_schema.get("description") or "").strip()
        lines.append(
            f"  {property_name} ({requirement}, {property_type}) — "
            f"{property_description[:120]}"
        )
    return "\n".join(lines)


def model_visible_tool_definitions(session: Any) -> list[dict[str, Any]]:
    """Read Tool Definitions available to model-facing Lark interfaces."""
    reader = getattr(session.harness, "model_visible_tool_definitions", None)
    if callable(reader):
        return list(reader())
    return list(session.harness.registry.list_tool_definitions())


def find_tool_schema(
    session: Any,
    tool_name: str,
) -> dict[str, Any] | None:
    """Find one model-visible Tool Definition by name."""
    return next(
        (
            schema
            for schema in model_visible_tool_definitions(session)
            if schema.get("name") == tool_name
        ),
        None,
    )


def build_help_text(session: Any | None) -> str:
    """Build the public command list without exposing internal tools."""
    lines = [
        "Available commands:",
        "",
        "[Admin]",
        "  /help            — show this message",
        "  /help <tool>     — show tool details",
        "  /status          — show session state and token usage",
        "  /skill           — list skills; /skill <name> loads one for the next task",
        "  /reset           — clear conversation memory",
        "  /cancel          — cancel while the agent waits for query_user",
    ]
    if session is None or session.harness.registry is None:
        lines.extend(
            [
                "",
                "(Create a session by sending a message before requesting "
                "the tool list.)",
            ]
        )
        return "\n".join(lines)

    tool_lines = [
        "  " + format_tool_usage(schema)
        for schema in model_visible_tool_definitions(session)
    ]
    if tool_lines:
        lines.extend(["", "[Tools]", *tool_lines])
    lines.extend(
        [
            "",
            "Tip: /<tool> submits the requested tool and arguments to the "
            "Agentic Loop. Model selection, argument validation, and hooks "
            "still apply.",
        ]
    )
    return "\n".join(lines)


class CommandRouter:
    """Route channel commands while preserving the Agentic Loop boundary."""

    def __init__(
        self,
        *,
        sessions: dict[str, Any],
        sessions_mutex: asyncio.Lock,
        session_ttl_sec: int,
        send_text: SendText,
        run_model_task: RunModelTask,
        fetch_images: FetchImages,
        close_session: CloseSession,
        return_to_menu: ReturnToMenu,
    ) -> None:
        self.sessions = sessions
        self.sessions_mutex = sessions_mutex
        self.session_ttl_sec = session_ttl_sec
        self.send_text = send_text
        self.run_model_task = run_model_task
        self.fetch_images = fetch_images
        self.close_session = close_session
        self.return_to_menu = return_to_menu

    async def handle_menu_input(
        self,
        parsed: ParsedMessage,
        session: Any,
    ) -> None:
        """Handle one numeric menu selection or enter natural-language mode."""
        text = parsed.text.strip()
        state = session.menu_state
        if not text.isdigit():
            session.menu_state = None
            await self.run_model_task(session, parsed, images=[])
            return

        choice = int(text)
        if isinstance(state, TopMenuState):
            await self._handle_top_menu_choice(parsed, session, choice)
            return
        await self.return_to_menu(session, parsed)

    async def _handle_top_menu_choice(
        self,
        parsed: ParsedMessage,
        session: Any,
        choice: int,
    ) -> None:
        if choice == 1:
            session.menu_state = None
            await self.send_text(
                parsed,
                "Tell me which object to bring. Your next message will be "
                "planned by the agent.",
            )
        elif choice == 2:
            session.menu_state = None
            parsed.text = (
                "Survey the current environment and tell the user what is "
                "visible. Select any required Observation tool through the "
                "normal Agentic Loop."
            )
            await self.run_model_task(session, parsed, images=[])
        elif choice == 3:
            session.menu_state = None
            await self.send_text(
                parsed,
                "📝 Enter your request in natural language.\n"
                "Following messages remain in model conversation mode. "
                "Send /menu to return to the menu.",
            )
        elif choice == 4:
            async with self.sessions_mutex:
                removed = self.sessions.pop(parsed.user_id, None)
            if removed is not None:
                await self.close_session(removed, lock_already_held=True)
            await self.send_text(
                parsed,
                "✓ The session has ended. Your next message will start a new one.",
            )
        else:
            await self.send_text(
                parsed,
                f"Invalid option {choice}. Reply with 1–4.\n\n" + render_top_menu(),
            )

    async def try_handle_admin_command(self, parsed: ParsedMessage) -> bool:
        """Handle a session-independent admin command when one is present."""
        text = parsed.text.strip()
        if not text.startswith("/"):
            return False
        command = text.split(None, 1)[0].lower()
        if command in ("/reset", "/clear"):
            await self._reset_session(parsed)
            return True
        if command == "/cancel":
            await self._cancel_task(parsed)
            return True
        if command == "/status":
            await self._send_status(parsed)
            return True
        return False

    async def _reset_session(self, parsed: ParsedMessage) -> None:
        async with self.sessions_mutex:
            session = self.sessions.pop(parsed.user_id, None)
        if session is None:
            await self.send_text(
                parsed,
                "(No active session. Your next message will create one.)",
            )
            return
        await self.close_session(session)
        await self.send_text(
            parsed,
            "✓ The session has been reset. Your next message will start fresh.",
        )

    async def _cancel_task(self, parsed: ParsedMessage) -> None:
        session = self.sessions.get(parsed.user_id)
        if session is None or not session.lock.locked():
            await self.send_text(parsed, "(No task is currently running.)")
            return
        session.cancel_event.set()
        await self.send_text(
            parsed,
            "✓ Cancellation requested. The Agentic Loop will stop after the "
            "current tool returns.",
        )

    async def _send_status(self, parsed: ParsedMessage) -> None:
        session = self.sessions.get(parsed.user_id)
        if session is None:
            await self.send_text(
                parsed,
                "Status: no active session. Your next message will create one.",
            )
            return
        idle = int(time.time() - session.last_used)
        message_count = (
            len(session.harness.ctx.accumulated_messages) if session.harness.ctx else 0
        )
        ttl_left = max(0, self.session_ttl_sec - idle)
        inner = getattr(session.harness.model, "_impl", session.harness.model)
        model = getattr(inner, "model", "?")
        lines = [
            "Session status:",
            f"  Model: {model}",
            f"  Context messages: {message_count}",
            f"  Idle: {idle}s (TTL remaining: {ttl_left}s)",
        ]
        if session.usage is not None:
            lines.append(
                f"  Cumulative tokens: {session.usage.get('input_tokens', 0):,} in · "
                f"{session.usage.get('output_tokens', 0):,} out"
            )
            if session.usage.get("cache_read_tokens") or session.usage.get(
                "cache_creation_tokens"
            ):
                lines.append(
                    "  Cache tokens: "
                    f"{session.usage.get('cache_read_tokens', 0):,} read · "
                    f"{session.usage.get('cache_creation_tokens', 0):,} write"
                )
        await self.send_text(parsed, "\n".join(lines))

    async def try_handle_tool_command(
        self,
        parsed: ParsedMessage,
        session: Any,
    ) -> bool:
        """Handle a session-aware slash command without direct physical calls."""
        text = parsed.text.strip()
        if not text.startswith("/"):
            return False
        parts = text.split(None, 1)
        command = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if command == "/menu":
            await self.return_to_menu(session, parsed)
        elif command in ("/skill", "/skills"):
            await self._handle_skill_command(parsed, session, rest)
        elif command in ("/help", "/?"):
            await self._handle_help_command(parsed, session, rest)
        else:
            await self._handle_registered_tool_command(
                parsed,
                session,
                command,
                command[1:],
                rest,
            )
        return True

    async def _handle_skill_command(
        self,
        parsed: ParsedMessage,
        session: Any,
        rest: str,
    ) -> None:
        skill_parts = rest.strip().split(None, 1)
        if not skill_parts:
            await self._send_available_skills(parsed, session)
            return

        skill_name = skill_parts[0].strip()
        task_text = skill_parts[1].strip() if len(skill_parts) > 1 else ""
        if skill_name.lower() in {"clear", "off", "none"}:
            session.pending_loaded_skill = None
            await self.send_text(parsed, "✓ The pending skill has been cleared.")
            return
        if not session.harness.registry.has("load_skill"):
            await self.send_text(
                parsed,
                "The current Harness has not registered `load_skill`.",
            )
            return

        loaded = await self._load_skill(session, skill_name)
        if not loaded.get("success"):
            await self._send_skill_load_failure(parsed, skill_name, loaded)
            return
        session.pending_loaded_skill = {
            "name": str(loaded.get("name") or skill_name),
            "description": str(loaded.get("description") or ""),
            "instructions": str(loaded.get("instructions") or ""),
            "resources": list(loaded.get("resources") or []),
        }
        if not task_text:
            await self.send_text(
                parsed,
                f"✓ Skill `{skill_name}` is loaded for the next natural-language task.",
            )
            return

        parsed.text = task_text
        images = await self.fetch_images(parsed)
        await self.send_text(
            parsed,
            f"✓ Skill `{skill_name}` is loaded. Starting task: {task_text}",
        )
        await self.run_model_task(session, parsed, images=images)

    async def _send_available_skills(
        self,
        parsed: ParsedMessage,
        session: Any,
    ) -> None:
        skills = list(getattr(session.harness, "skills", []) or [])
        if not skills:
            await self.send_text(parsed, "No skills are currently registered.")
            return
        pending_name = str((session.pending_loaded_skill or {}).get("name") or "")
        lines = ["Available skills:"]
        for skill in skills:
            marker = " (loaded for the next task)" if skill.name == pending_name else ""
            lines.append(f"- `{skill.name}`: {skill.description}{marker}")
        lines.extend(
            [
                "",
                "Load: `/skill <name>`",
                "Run immediately: `/skill <name> <task>`",
                "Clear: `/skill clear`",
            ]
        )
        await self.send_text(parsed, "\n".join(lines))

    @staticmethod
    async def _load_skill(session: Any, skill_name: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                session.harness.registry.call_tool,
                "load_skill",
                {"name": skill_name},
            )
        except Exception as exc:
            return {
                "success": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    async def _send_skill_load_failure(
        self,
        parsed: ParsedMessage,
        skill_name: str,
        loaded: dict[str, Any],
    ) -> None:
        available = loaded.get("available_skills") or []
        suffix = f"\nAvailable: {', '.join(available)}" if available else ""
        await self.send_text(
            parsed,
            f"✗ Failed to load skill `{skill_name}`: "
            f"{loaded.get('reason') or loaded.get('error') or '?'}{suffix}",
        )

    async def _handle_help_command(
        self,
        parsed: ParsedMessage,
        session: Any,
        rest: str,
    ) -> None:
        target = rest.strip().lstrip("/")
        if not target:
            await self.send_text(parsed, build_help_text(session))
            return
        schema = find_tool_schema(session, target)
        if schema is None:
            await self.send_text(
                parsed,
                f"Unknown tool: {target}. Use /help to list available tools.",
            )
            return
        await self.send_text(parsed, format_tool_help(schema))

    async def _handle_registered_tool_command(
        self,
        parsed: ParsedMessage,
        session: Any,
        command: str,
        tool_name: str,
        rest: str,
    ) -> None:
        schema = find_tool_schema(session, tool_name)
        if schema is None:
            await self.send_text(
                parsed,
                f"Unknown command {command}.\n\nUse /help to list available commands.",
            )
            return
        arguments = parse_slash_args(rest)
        required = set((schema.get("inputSchema") or {}).get("required", []) or [])
        missing = required - set(arguments)
        if missing:
            await self.send_text(
                parsed,
                f"Missing parameters: {', '.join(sorted(missing))}\n\n"
                f"Usage: {format_tool_usage(schema)}\n\n"
                f"Details: /help {tool_name}",
            )
            return

        parsed.text = (
            f"The user requested the registered tool `{tool_name}` through the "
            "Lark command interface with these arguments: "
            f"{json.dumps(arguments, ensure_ascii=False, default=str)}. "
            "Handle this request through the normal Agentic Loop. Select and call "
            "the tool only if it is appropriate under the current evidence, "
            "validation rules, and hooks."
        )
        images = await self.fetch_images(parsed)
        await self.run_model_task(session, parsed, images=images)
