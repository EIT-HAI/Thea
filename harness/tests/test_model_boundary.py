from __future__ import annotations

from types import SimpleNamespace

import pytest
from harness.models.anthropic_adapter import anthropic_response_to_model_response
from harness.models.openai_adapter import openai_response_to_model_response


def test_anthropic_tool_arguments_are_normalized_to_a_mapping() -> None:
    response = anthropic_response_to_model_response(
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id="call-1",
                    name="move_base",
                    input=["invalid", "shape"],
                )
            ],
            stop_reason="tool_use",
        )
    )

    assert response.tool_calls[0].arguments == {"_value": ["invalid", "shape"]}


def test_openai_tool_arguments_are_normalized_to_a_mapping() -> None:
    response = openai_response_to_model_response(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="move_base",
                                    arguments='["invalid", "shape"]',
                                ),
                            )
                        ],
                    ),
                )
            ]
        )
    )

    assert response.tool_calls[0].arguments == {"_value": ["invalid", "shape"]}


@pytest.mark.parametrize(
    ("adapter", "response"),
    [
        (
            anthropic_response_to_model_response,
            SimpleNamespace(
                content=[SimpleNamespace(type="text", text=["not", "text"])],
                stop_reason="end_turn",
            ),
        ),
        (
            openai_response_to_model_response,
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=["not", "text"],
                            reasoning_content="",
                            tool_calls=[],
                        ),
                    )
                ]
            ),
        ),
    ],
)
def test_provider_adapters_reject_non_text_model_content(adapter, response) -> None:
    with pytest.raises(ValueError, match="text content must be a string"):
        adapter(response)
