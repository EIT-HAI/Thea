# Memory, Skills, and Embodiment

## Memory lifecycle

`FileMemory` implements three forms with different lifetimes:

```text
MEMORY.md
TASK_NOTES.md
tool_experience/
  pick_object.md
```

`MEMORY.md` contains:

```markdown
# Memory

## Preferences
- [user-preference] Prefer concise completion reports.
## Conventions
- [convention] Ask before choosing among equally plausible destinations.
## General Lessons
- [general-lesson] Use current physical evidence before local action.
```

The sections provide the durable scope; the labels make each entry's kind
visible in an abridged artifact or prompt excerpt. Only these three controlled
sections enter Resident context. Other Markdown sections, if present in an
operator-managed file, are not projected to the model.

Each tool ledger contains:

```markdown
# Tool Experience: pick_object

## Success
## Failure
```

Use the LLM-backed reference components:

```python
from harness import (
    FileMemory,
    ModelMemoryConsolidator,
)

memory_model = build_memory_model()
memory = FileMemory(
    "./runtime/memory",
    consolidator=ModelMemoryConsolidator(memory_model),
)
```

When the reference `ModelMemoryConsolidator` is used, `FileMemory`
automatically uses the same model through `ModelToolExperienceSummarizer`.
Pass another `ToolExperienceSummarizerProtocol` implementation only when the
deployment needs a different summarization policy.

During a task, the Harness resets Task Notes, records compact events, and
renders changed snapshots before model decisions. When a task completes
normally, the consolidator extracts accepted durable Memory entries and
tool-scoped Success/Failure experience; Task Notes then expire. Aborted tasks
skip durable consolidation but still expire their Task Notes. `FileMemory`
accepts only entries at or above its configured confidence threshold (default
`0.75`) and rejects transient refs or one-run state. Tool success lessons
require an evaluator-confirmed success in the completed trajectory. Failure
lessons require a failure followed by evaluator-confirmed success of the same
tool, so an unrelated successful action cannot validate a tool-specific
diagnosis.

At the next task start, `MEMORY.md` enters Resident context and each tool
ledger is summarized by the configured model into its Tool Definition.
Applications can replace the LLM-backed components through
`MemoryConsolidatorProtocol` and `ToolExperienceSummarizerProtocol`. Without a
consolidator, durable task-end writes are an explicit no-op. If Tool Experience
files exist, a custom non-model consolidator must be paired with an explicit
summarizer; the Harness never substitutes concatenated ledger entries for the
model-generated summary described above.

## Embodiment Profile

The profile is a deployment-owned Markdown document with three sections and
seven required items:

```markdown
# Embodiment Profile

## Operational Envelope

### Base Footprint
Circular footprint with radius 0.38 m.

### Base Mobility
Planar translation and yaw rotation.

### Reachable Workspace
End-effector operating region relative to the base.

## Perception Configuration

### Sensor Modalities
RGB images, depth, and four-direction base clearance.

### Model-Visible Views
front, torso, wrist

## Base-Relative Positions

### Camera Positions
Fixed camera positions relative to the base center.

### Initial Gripper Positions
Initial gripper positions relative to the base center.
```

Configure its path:

```yaml
embodiment_profile_file: ./profiles/robot.md
```

When a Profile is configured, `Harness` validates this required structure at
startup. `load_embodiment_profile_document_from_config()` exposes the same
validation boundary to deployment code.

## Skills

Each directory-backed Skill uses `skills/<name>/SKILL.md`:

```markdown
---
name: tidy-workspace
description: Decide what to remove, retain, and report while tidying a desk.
---

## Working order

...
```

```yaml
skills:
  dir: ./skills
```

Only the Skill name and description enter Resident context by default. The
model uses `load_skill` to load the body and `load_skill_resource` to load one
declared UTF-8 resource. Loaded Skill content expires at task end.

Skills provide task instructions. Tools and hooks remain the execution,
authorization, and safety boundaries.
