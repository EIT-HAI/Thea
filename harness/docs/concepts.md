# Runtime and Context

## Execution units

The Harness uses three nested time units:

| Unit | Definition |
|---|---|
| Turn | One model decision followed by at most one selected tool execution. |
| Task | One user instruction, from its first turn to a final response or Harness termination. |
| Session | Multiple tasks that may share Accumulated messages and deployment resources. |

A task begins by resetting Task Notes. Before every model call, the Harness:

1. appends a new Task Notes snapshot when the notes changed;
2. obtains a new Observation and replaces the previous one;
3. refreshes the working Scene Graph and renders a compact Brief;
4. applies configured Accumulated-context compaction.

The Embodiment Profile and Skill Catalog are selected when the Harness session
is created. At task start, the Harness reads Memory and tool-experience
summaries and assembles the visible Tool Definitions. If the model selects
`load_skill`, the chosen Skill body joins Resident context for the rest of
that task.

The model returns text, at most one selected Tool Call, or both. Tool
text can be surfaced as progress without consuming another physical turn. The
Harness validates and executes the selected tool, runs configured hooks,
records a compact result, and starts the next turn from refreshed evidence.

A task ends when the model returns no Tool Call or when the Harness terminates
the run. The final text becomes the task report. Fully consuming
`run_stream()` produces exactly one terminal `done` event.

## Context lifetimes

The `Context` object keeps three lifetimes separate:

| Lifetime | Contents | Update rule |
|---|---|---|
| Resident | System Prompt, Memory, Embodiment Profile, Tool Definitions, Skill Catalog, loaded Skill body | Available across turns; a selected Skill body may be added for the rest of the task. |
| Refreshed | Scene Graph Brief and current Observation | Replaced before every model decision. |
| Accumulated | Instructions, Task Notes, Model Responses, compact Tool Results | Grows through ordinary turns and may be compacted. |

Observation images and transient Tool Result images remain available for the
model call that consumes them, then leave the context. The underlying working
Scene Graph, durable Memory files, and deployment providers have their own
lifecycle and are not stored as an unbounded message transcript.

## Compaction

Compaction operates only on Accumulated context. It triggers when the estimated
model context reaches `context_window - reserve_tokens`, leaving room for the
next response. The Harness replaces an older prefix with a checkpoint summary
while retaining recent complete turns verbatim. The retained tail is bounded
by both turn count and an estimated token budget. Resident context, Refreshed
context, the current Task Notes snapshot, and transient image evidence are
never summarized.

Stored media is omitted from the summarization request. Verbose Tool Results
and reasoning are bounded while preserving their beginning and end. If the
older prefix is still too large for one summary request, the Harness processes
it in ordered rounds. Each round updates the previous checkpoint with the next
message chunk, so an input limit never silently removes the middle of the
history. The final checkpoint has stable `kind` and `source` metadata, making
subsequent compactions and run-log inspection explicit. A failed or malformed
summary leaves the original Accumulated context unchanged and does not
permanently disable later attempts.

```yaml
context:
  compaction:
    enabled: true
    context_window: 200000
    reserve_tokens: 16384
    keep_recent_turns: 4
    keep_recent_tokens: 20000
    tool_result_max_chars: 2000
    reasoning_max_chars: 2000
    max_input_chars: 400000
    max_summary_rounds: 8
```

If no dedicated `compaction_model` is supplied, the Harness uses its main
model. Compaction inputs are sanitized and partitioned into bounded requests.
Binary and base64 image payloads are represented by bounded placeholders.

## Replanning and termination

The Agentic Loop is reactive rather than a fixed multi-step plan:

```text
refresh -> decide -> execute one selected tool -> record -> refresh
```

`query_user` and `notify_user` remain model-selected user-interaction tools.
Post-execution `evaluate_run` is different: the Harness derives and invokes it
as a hidden hook after a configured physical tool. It is not a second
model-selected action.

`run_stream(..., poll_replan=callback)` can accept an instruction revision
inside the active task. `run_stream(..., should_cancel=callback)` checks
cooperative cancellation before model decisions and tool execution. A tool
that is already blocking needs a deployment-owned interrupt path.

## Session lifecycle

One `Harness` instance owns one session and rejects concurrent tasks.
`reset_session()` clears Accumulated context while retaining clients and
registered tools. `close()` releases owned MCP subprocesses and objects passed
through `owned_resources`.

The application should create separate Harness instances for genuinely
independent sessions and serialize access when they share physical hardware.
