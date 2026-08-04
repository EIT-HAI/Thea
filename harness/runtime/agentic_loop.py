"""Task-scoped Agentic Loop orchestration.

The dependency-composing :class:`harness.runtime.core.Harness` provides context,
execution, and lifecycle boundaries. This module owns the paper's control
flow: one model decision and at most one selected Tool Call per turn, followed
by exactly one final ``done`` event for every fully consumed task stream.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from typing import Any

from harness.context import ModelResponse
from harness.models import ModelCallError
from harness.runtime.task import (
    TaskOutcome,
    TaskState,
    abnormal_model_stop,
    poll_cancellation,
    revised_instruction_message,
    task_done_event,
    user_interaction_abort_text,
)
from harness.runtime.task import poll_replan as poll_replan_callback
from harness.skills.runtime import skill_loaded_event
from harness.tools.evidence import build_user_content


class AgenticLoop:
    """Execute one model decision and at most one selected tool per turn."""

    def _poll_replan_events(
        self,
        task: TaskState,
        callback: Callable[[], str] | None,
        *,
        turn: int,
    ) -> list[dict[str, Any]]:
        assert self.ctx is not None
        outcome = poll_replan_callback(callback, turn=turn)
        events = [outcome.error_event] if outcome.error_event is not None else []
        if not outcome.value:
            return events
        task.revise_instruction(outcome.value)
        self.memory.task_notes.revise_goal(outcome.value)
        self.ctx.add_user_message(revised_instruction_message(outcome.value))
        events.append(
            {
                "type": "replan_requested",
                "turn": turn,
                "text": outcome.value,
            }
        )
        return events

    def run_stream(
        self,
        instruction: str,
        max_turns: int = 100,
        failure_budget: int = 20,
        images: list[bytes] | None = None,
        poll_replan: Callable[[], str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        system_prompt_override: str | None = None,
        loaded_skill: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Run one instruction and finish with exactly one task outcome."""
        self._begin_task_lifetime()
        task_scope_released = False
        self._current_task_turn = 0
        try:
            try:
                outcome = yield from self._run_task_stream(
                    instruction,
                    max_turns=max_turns,
                    failure_budget=failure_budget,
                    images=images,
                    poll_replan=poll_replan,
                    should_cancel=should_cancel,
                    system_prompt_override=system_prompt_override,
                    loaded_skill=loaded_skill,
                )
            except GeneratorExit:
                raise
            except Exception as exc:
                turn = max(0, int(self._current_task_turn))
                yield {
                    "type": "harness_error",
                    "turn": turn,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                outcome = TaskOutcome(
                    task_status="aborted",
                    termination_reason="harness_error",
                    final_text=f"The harness failed: {type(exc).__name__}: {exc}",
                    turns=turn,
                )

            consolidation_event = self._task_end_consolidation_event(
                turn=outcome.turns,
                termination_reason=outcome.termination_reason,
                consolidate=outcome.task_status == "completed",
            )
            self._clear_task_scope()
            self._end_task_lifetime()
            task_scope_released = True

            yield consolidation_event
            yield task_done_event(outcome)
        finally:
            if not task_scope_released:
                self._clear_task_scope()
                self._end_task_lifetime()
            self._current_task_turn = 0

    def _run_task_stream(
        self,
        instruction: str,
        max_turns: int = 100,
        failure_budget: int = 20,
        images: list[bytes] | None = None,
        poll_replan: Callable[[], str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        system_prompt_override: str | None = None,
        loaded_skill: dict[str, Any] | None = None,
    ) -> Generator[dict[str, Any], None, TaskOutcome]:
        """Run until completion, cancellation, failure, or a configured budget."""
        continuation = self.ctx is not None
        task = TaskState.create(
            instruction,
            max_turns=max_turns,
            failure_budget=failure_budget,
        )
        task_skill = self.skill_system.begin_task(loaded_skill)
        self._load_task_context()
        assert self.ctx is not None
        self.memory.task_notes.reset(task.instruction)
        self.ctx.add_user_message(build_user_content(task.instruction, images))

        yield {
            "type": "user_message",
            "text": task.instruction,
            "n_images": len(images or []),
            "continuation": continuation,
            "loaded_skill": str((task_skill or {}).get("name") or ""),
        }
        if task_skill:
            yield skill_loaded_event(task_skill)
        yield {
            "type": "tools_listed",
            "names": [
                definition["name"] for definition in self._visible_tool_definitions
            ],
            "count": len(self._visible_tool_definitions),
            "scope": "task",
        }

        for turn in range(1, task.turn_budget + 1):
            self._current_task_turn = turn
            outcome = yield from self._run_turn(
                task,
                turn=turn,
                poll_replan=poll_replan,
                should_cancel=should_cancel,
                system_prompt_override=system_prompt_override,
            )
            if outcome is not None:
                return outcome

        final_text = (
            f"Stopped after max_turns={task.turn_budget}; "
            "the model continued requesting tools."
        )
        yield {
            "type": "turn_budget_exhausted",
            "turns": task.turn_budget,
            "turn_budget": task.turn_budget,
            "consecutive_failures": task.consecutive_failures,
            "final_text": final_text,
        }
        return TaskOutcome(
            task_status="aborted",
            termination_reason="turn_budget_exhausted",
            final_text=final_text,
            turns=task.turn_budget,
        )

    def _run_turn(
        self,
        task: TaskState,
        *,
        turn: int,
        poll_replan: Callable[[], str] | None,
        should_cancel: Callable[[], bool] | None,
        system_prompt_override: str | None,
    ) -> Generator[dict[str, Any], None, TaskOutcome | None]:
        """Refresh context, request one decision, and execute at most one tool."""
        cancellation = poll_cancellation(should_cancel, turn=turn)
        if cancellation.error_event is not None:
            yield cancellation.error_event
        if cancellation.value:
            return _cancelled_outcome(
                turn=turn - 1,
                reason="cancelled_before_model_decision",
                text="Task cancelled before the next model decision.",
            )

        yield from self._build_context(turn, system_prompt_override)
        yield self._prompt_snapshot(turn)
        response, request_outcome = yield from self._request_model_decision(turn)
        if request_outcome is not None:
            return request_outcome
        assert response is not None

        response_outcome = yield from self._interpret_model_response(
            response,
            turn=turn,
        )
        if response_outcome is not None:
            return response_outcome

        cancellation = poll_cancellation(should_cancel, turn=turn)
        if cancellation.error_event is not None:
            yield cancellation.error_event
        if cancellation.value:
            yield from self._record_unexecuted_tool_calls(
                response,
                turn=turn,
                reason="Task cancelled after the model decision.",
                kind="task_cancelled",
            )
            return _cancelled_outcome(
                turn=turn,
                reason="cancelled_after_model_decision",
                text="Task cancelled before the selected tool could execute.",
            )

        return (
            yield from self._execute_selected_tool(
                response,
                task,
                turn=turn,
                poll_replan=poll_replan,
                should_cancel=should_cancel,
            )
        )

    def _request_model_decision(
        self,
        turn: int,
    ) -> Generator[
        dict[str, Any],
        None,
        tuple[ModelResponse | None, TaskOutcome | None],
    ]:
        """Call the model and expose one provider-neutral decision event."""
        try:
            response, model_response_event = self._call_model(turn)
        except ModelCallError as exc:
            yield {
                "type": "model_error",
                "turn": turn,
                "provider": exc.provider,
                "attempts": exc.attempts,
                "error": str(exc),
            }
            return (
                None,
                TaskOutcome(
                    task_status="aborted",
                    termination_reason="model_call_failed",
                    final_text=f"Model request failed: {exc}",
                    turns=turn,
                ),
            )

        decision_handed_off = False
        try:
            yield model_response_event
            if response.text and response.tool_calls:
                yield {
                    "type": "say",
                    "turn": turn,
                    "text": response.text,
                    "has_tool_call": True,
                }
            decision_handed_off = True
            return response, None
        finally:
            if not decision_handed_off and response.tool_calls:
                self._record_unexecuted_tool_calls_now(
                    response,
                    turn=turn,
                    reason="The task stream ended before the Tool Call executed.",
                    kind="task_stream_closed",
                )

    def _interpret_model_response(
        self,
        response: ModelResponse,
        *,
        turn: int,
    ) -> Generator[dict[str, Any], None, TaskOutcome | None]:
        """Interpret provider stop state before any physical execution."""
        stop_outcome = abnormal_model_stop(
            getattr(response, "stop_reason", ""),
            has_tool_calls=bool(response.tool_calls),
        )
        if stop_outcome is not None:
            yield from self._record_unexecuted_tool_calls(
                response,
                turn=turn,
                reason=stop_outcome.final_text,
                kind=stop_outcome.termination_reason,
            )
            yield {
                "type": "model_stop",
                "turn": turn,
                "stop_reason": stop_outcome.stop_reason,
                "task_status": stop_outcome.task_status,
                "termination_reason": stop_outcome.termination_reason,
            }
            return TaskOutcome(
                task_status=stop_outcome.task_status,
                termination_reason=stop_outcome.termination_reason,
                final_text=stop_outcome.final_text,
                turns=turn,
                stop_reason=stop_outcome.stop_reason,
            )

        if response.tool_calls:
            return None
        return TaskOutcome(
            task_status="completed",
            termination_reason="model_returned_no_tool_call",
            final_text=response.text,
            turns=turn,
            stop_reason=str(getattr(response, "stop_reason", "") or ""),
        )

    def _execute_selected_tool(
        self,
        response: ModelResponse,
        task: TaskState,
        *,
        turn: int,
        poll_replan: Callable[[], str] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> Generator[dict[str, Any], None, TaskOutcome | None]:
        """Execute the selected Tool Call under its evaluator boundary."""
        call = response.tool_calls[0]
        assert self.ctx is not None
        execution = self._execution_pipeline().execute_selected_tool_call(
            call,
            context=self.ctx,
            turn=turn,
            should_cancel=should_cancel,
        )
        rejected_call_events = self._reject_extra_tool_calls(
            response.tool_calls[1:],
            turn,
        )

        if not execution.cancelled:
            task.record_turn_outcome(
                execution.result,
                execution.evaluator_verdict,
            )

        yield from execution.events
        yield from rejected_call_events

        if execution.cancelled:
            reason = execution.cancellation_reason or "cancelled_before_tool_execution"
            text = (
                "Task cancelled between action segments."
                if reason == "cancelled_between_action_segments"
                else "Task cancelled before the selected tool could execute."
            )
            return _cancelled_outcome(
                turn=turn,
                reason=reason,
                text=text,
            )

        abort_text = user_interaction_abort_text(execution.result)
        if abort_text is not None:
            result_kind = str(execution.result.get("kind") or "")
            return TaskOutcome(
                task_status="aborted",
                termination_reason=result_kind or "user_interaction_ended",
                final_text=abort_text,
                turns=turn,
            )

        if task.failure_budget_exhausted:
            final_text = (
                f"Stopped after failure_budget={task.failure_budget}; "
                "the last tool did not succeed."
            )
            yield {
                "type": "failure_budget_exhausted",
                "turns": turn,
                "consecutive_failures": task.consecutive_failures,
                "failure_budget": task.failure_budget,
                "final_text": final_text,
            }
            return TaskOutcome(
                task_status="aborted",
                termination_reason="failure_budget_exhausted",
                final_text=final_text,
                turns=turn,
            )

        yield from self._poll_replan_events(
            task,
            poll_replan,
            turn=turn,
        )
        return None

    def _record_unexecuted_tool_calls(
        self,
        response: ModelResponse,
        *,
        turn: int,
        reason: str,
        kind: str,
    ) -> Generator[dict[str, Any], None, None]:
        yield from self._record_unexecuted_tool_calls_now(
            response,
            turn=turn,
            reason=reason,
            kind=kind,
        )

    def _record_unexecuted_tool_calls_now(
        self,
        response: ModelResponse,
        *,
        turn: int,
        reason: str,
        kind: str,
    ) -> list[dict[str, Any]]:
        assert self.ctx is not None
        return self._execution_pipeline().record_unexecuted_calls(
            response.tool_calls,
            turn=turn,
            context=self.ctx,
            reason=reason,
            kind=kind,
        )

    def run(
        self,
        instruction: str,
        max_turns: int = 100,
        failure_budget: int = 20,
    ) -> str:
        """Run one task and return its final user-facing text."""
        final_text = ""
        for event in self.run_stream(
            instruction,
            max_turns=max_turns,
            failure_budget=failure_budget,
        ):
            if event.get("type") == "done":
                final_text = str(event.get("final_text") or "")
        return final_text


def _cancelled_outcome(
    *,
    turn: int,
    reason: str,
    text: str,
) -> TaskOutcome:
    return TaskOutcome(
        task_status="aborted",
        termination_reason=reason,
        final_text=text,
        turns=max(0, turn),
    )


__all__ = ["AgenticLoop"]
