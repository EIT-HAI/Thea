"""Persist ``Harness.run_stream()`` events to a JSONL file.

Each line is one event dict from run_stream(), JSON-encoded. Lets you
replay, search, or compare one task run without re-spawning MCP subprocesses.

By default, logs are written below the user's state directory:
``$THEA_HARNESS_LOG_DIR`` when set, otherwise
``$XDG_STATE_HOME/thea-harness/runs`` or
``~/.local/state/thea-harness/runs``.

Usage:
    from harness.observability import run_logger

    with run_logger() as (log_path, log):
        for ev in harness.run_stream(instruction):
            log(ev)
            render(ev)
    print(f"saved {log_path}")

Inspecting:
    # print the selected directory
    from harness.observability import default_log_dir
    print(default_log_dir())
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.context.redaction import redact_sensitive_data

LOG_DIR_ENV = "THEA_HARNESS_LOG_DIR"
XDG_STATE_HOME_ENV = "XDG_STATE_HOME"
DEFAULT_LOG_SUBDIR = Path("thea-harness") / "runs"
DEFAULT_MAX_EVENT_BYTES = 2_000_000


def _absolute_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def default_log_dir() -> Path:
    """Return the absolute directory used by ``run_logger()`` by default.

    ``THEA_HARNESS_LOG_DIR`` is an explicit override. Otherwise the path
    follows the XDG state-directory convention and never defaults to creating
    an ``output`` directory in the process working tree.
    """
    configured = os.environ.get(LOG_DIR_ENV, "").strip()
    if configured:
        return _absolute_path(configured)

    xdg_state_home = os.environ.get(XDG_STATE_HOME_ENV, "").strip()
    xdg_path = Path(xdg_state_home).expanduser()
    state_home = (
        xdg_path.resolve(strict=False)
        if xdg_state_home and xdg_path.is_absolute()
        else Path.home() / ".local" / "state"
    )
    return (state_home / DEFAULT_LOG_SUBDIR).resolve(strict=False)


def _default_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return default_log_dir() / f"run_{timestamp}.jsonl"


@contextmanager
def run_logger(
    path: Path | str | None = None,
    *,
    redactor: Callable[[Any], Any] | None = redact_sensitive_data,
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
) -> Iterator[tuple[Path, Callable[[dict[str, Any]], None]]]:
    """Open a JSONL run log. Yield (path, log_fn).

    Each call to log_fn(event) appends one line + flushes (so a kill -9 mid-run
    still leaves a usable partial log). New logs are private to the current
    user, sensitive fields are redacted by default, and oversized events are
    replaced with a bounded diagnostic record.
    """
    log_path = Path(path) if path else _default_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        log_path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    os.chmod(log_path, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as fh:

        def log(ev: dict[str, Any]) -> None:
            safe_event = redactor(ev) if redactor is not None else ev
            payload = json.dumps(safe_event, ensure_ascii=False, default=str)
            encoded_size = len(payload.encode("utf-8"))
            if encoded_size > max(1, int(max_event_bytes)):
                payload = json.dumps(
                    {
                        "type": "log_event_omitted",
                        "original_type": str(ev.get("type") or ""),
                        "encoded_bytes": encoded_size,
                        "max_event_bytes": int(max_event_bytes),
                    },
                    ensure_ascii=False,
                )
            fh.write(payload + "\n")
            fh.flush()

        yield log_path, log


def read_run(path: Path | str) -> list[dict[str, Any]]:
    """Load a run log back into a list of event dicts. Useful for tests."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
