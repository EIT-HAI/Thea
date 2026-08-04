from __future__ import annotations

from types import SimpleNamespace

import pytest
from harness.mcp.client import (
    MCPClient,
    _call_tool_timeout_sec,
    _parse_call_tool_result,
)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_model_timeout_hint_falls_back_when_not_finite_and_positive(value) -> None:
    assert (
        _call_tool_timeout_sec(
            {"timeout_sec": value},
            default_timeout=30.0,
            argument_margin=5.0,
        )
        == 30.0
    )


def test_explicit_invalid_timeout_is_rejected_before_tool_execution() -> None:
    class Runner:
        called = False

        def call_tool(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("tool must not execute")

    runner = Runner()
    client = MCPClient.__new__(MCPClient)
    client._tools = {
        "move_base": {
            "server": "robot",
            "default_timeout": 30.0,
            "argument_margin": 5.0,
        }
    }
    client._sessions = {"robot": runner}
    client._execution_status_unknown = ""

    result = client.call_tool("move_base", {}, timeout=0)

    assert result["success"] is False
    assert result["kind"] == "invalid_mcp_timeout"
    assert runner.called is False


def test_mcp_error_flag_cannot_be_reported_as_success() -> None:
    result = _parse_call_tool_result(
        SimpleNamespace(
            isError=True,
            structuredContent={
                "result": {
                    "success": True,
                    "value": "untrusted success",
                    "message": "policy failed",
                }
            },
            content=[],
        )
    )

    assert result["success"] is False
    assert result["kind"] == "mcp_tool_error"
    assert result["reason"] == "policy failed"


def test_mcp_success_result_is_preserved() -> None:
    payload = {"success": True, "pose": {"x": 1.0}}

    assert (
        _parse_call_tool_result(
            SimpleNamespace(
                isError=False,
                structuredContent={"result": payload},
                content=[],
            )
        )
        == payload
    )
