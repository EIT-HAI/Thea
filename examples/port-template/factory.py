"""Composition root for one robot deployment."""

from __future__ import annotations

from typing import NoReturn

from harness import Harness, ToolRegistry, register_scene_graph_query_tools

from .tools import RobotClient, register_robot_tools


def create_harness(config, session_context) -> Harness:
    """Connect deployment resources and return one channel-scoped Harness."""
    del session_context
    robot = connect_robot()
    registry = ToolRegistry()
    register_robot_tools(registry, robot)

    scene_graph = build_scene_graph(robot)
    register_scene_graph_query_tools(registry, scene_graph)

    return Harness(
        config,
        model=build_model(config),
        observation_provider=build_observation_provider(robot),
        base_clearance_provider=build_base_clearance_provider(robot),
        registry=registry,
        scene_graph=scene_graph,
        evaluator=build_evaluator(config),
        post_execution_observation_provider=(
            build_post_execution_observation_provider(robot)
        ),
        owned_resources=(robot,),
    )


def connect_robot() -> RobotClient:
    """Create the deployment-owned robot SDK adapter."""
    return _replace("connect_robot")


def build_model(config):
    """Create a ModelProtocol implementation or use a built-in model adapter."""
    del config
    return _replace("build_model")


def build_observation_provider(robot: RobotClient):
    """Return the current Observation before each model decision."""
    del robot
    return _replace("build_observation_provider")


def build_base_clearance_provider(robot: RobotClient):
    """Return fresh four-direction clearance for context and base-motion checks."""
    del robot
    return _replace("build_base_clearance_provider")


def build_scene_graph(robot: RobotClient):
    """Return the deployment-owned persistent Scene Graph boundary."""
    del robot
    return _replace("build_scene_graph")


def build_evaluator(config):
    """Return an EvaluatorProtocol implementation."""
    del config
    return _replace("build_evaluator")


def build_post_execution_observation_provider(robot: RobotClient):
    """Return objective evidence associated with a completed physical run."""
    del robot
    return _replace("build_post_execution_observation_provider")


def _replace(boundary: str) -> NoReturn:
    raise NotImplementedError(
        f"Replace {boundary} with a deployment-specific implementation."
    )
