from __future__ import annotations

from harness.context import Context, ObservationSink
from harness.world.observation import (
    BaseClearance,
    Observation,
    VisualEvidence,
    publish_observation,
    render_observation_messages,
)


def test_observation_rendering_is_provider_neutral() -> None:
    observation = Observation(
        visuals=(
            VisualEvidence(
                name="front",
                url="https://example.test/front.png",
                caption="Front view",
            ),
        ),
        base_clearance=BaseClearance(
            forward_m=0.8,
            backward_m=1.2,
            left_m=0.5,
            right_m=0.75,
        ),
        captured_at="2026-07-26T12:00:00Z",
        provenance="example_provider",
    )

    messages = render_observation_messages(observation)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["kind"] == "observation"
    content = messages[0]["content"]
    assert content[0]["type"] == "text"
    assert "valid for this decision only" in content[0]["text"]
    assert "forward=0.8 m" in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "https://example.test/front.png"},
        "caption": "Front view",
    }


def test_publish_observation_replaces_the_previous_current_evidence() -> None:
    context = Context()
    context.add_user_message("This instruction must remain accumulated.")
    sink = ObservationSink(context)

    first_event = publish_observation(
        sink,
        Observation(
            visuals=(
                VisualEvidence(
                    name="front",
                    url="https://example.test/first.png",
                ),
            ),
        ),
        turn=1,
    )
    second_event = publish_observation(
        sink,
        Observation(
            visuals=(
                VisualEvidence(
                    name="wrist",
                    url="https://example.test/second.png",
                ),
            ),
        ),
        turn=2,
    )

    assert first_event["visual_outputs"][0]["view"] == "front"
    assert second_event["visual_outputs"][0]["view"] == "wrist"
    assert len(context.observation_messages) == 1
    assert "second.png" in str(context.observation_messages)
    assert "first.png" not in str(context.observation_messages)
    assert context.accumulated_messages == [
        {"role": "user", "content": "This instruction must remain accumulated."}
    ]


def test_publish_observation_bounds_visual_outputs_for_lark() -> None:
    observation = Observation(
        visuals=tuple(
            VisualEvidence(
                name=f"view-{index}",
                url=f"https://example.test/{index}.png",
            )
            for index in range(4)
        ),
        summary="x" * 100,
    )
    context = Context()

    event = publish_observation(
        ObservationSink(context),
        observation,
        turn=3,
        max_visuals=2,
        max_summary_chars=20,
    )

    assert event["type"] == "observation"
    assert event["n_images"] == 2
    assert event["base_clearance_available"] is False
    assert event["base_clearance"] == {}
    assert event["visual_outputs_omitted"] == 2
    assert len(event["visual_outputs"]) == 2
    assert event["visual_outputs"][0] == {
        "type": "image_url",
        "image_url": {"url": "https://example.test/0.png"},
        "caption": "view-0",
        "view": "view-0",
        "label": "view-0",
    }
    assert len(event["summary"]) == 20
    assert event["summary"].endswith("…")


def test_image_bytes_are_rendered_as_data_urls() -> None:
    evidence = VisualEvidence.from_bytes(
        "front",
        b"\x89PNG\r\n\x1a\npayload",
    )

    block = evidence.visual_output()

    assert block["type"] == "image_url"
    assert block["image_url"]["url"].startswith("data:image/png;base64,")


def test_empty_observation_clears_current_messages() -> None:
    context = Context()
    context.set_observation_messages([{"role": "user", "content": "stale"}])

    event = publish_observation(
        ObservationSink(context),
        Observation(),
        turn=4,
    )

    assert context.observation_messages == []
    assert event["status"] == "unavailable"
    assert event["success"] is False
    assert event["visual_outputs"] == []
