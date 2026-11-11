r"""Optional full-content log of model calls, for debugging prompts and model answers.

Off by default. Set the environment variable XN_CALL_LOG to a folder, for example

    $env:XN_CALL_LOG = "data/call_logs"

and every model call made through run_logged or run_compact appends one JSON line to
<folder>/<stage>.jsonl: the run_id (the same as in llm_runs), time, stage, chapter, model,
ok, the error if any, and every message of the exchange (system prompt, user prompt, each
model answer, each retry message). A failed call is logged too, with the messages it got
to. A failure to write the log is a warning only; it never fails the call.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any

from pydantic_ai.messages import ModelMessagesTypeAdapter

logger = logging.getLogger(__name__)

CALL_LOG_ENV = "XN_CALL_LOG"
_lock = threading.Lock()


def call_log_dir() -> Path | None:
    r"""The folder to log calls into, or None when logging is off."""
    value = os.environ.get(CALL_LOG_ENV, "").strip()
    return Path(value) if value else None


def log_call(
    *,
    run_id: str,
    stage: str,
    chapter_id: int | None,
    model: str,
    messages: list[Any],
    error: BaseException | None = None,
) -> None:
    r"""Append one call to <XN_CALL_LOG>/<stage>.jsonl; does nothing when XN_CALL_LOG is unset."""
    folder = call_log_dir()
    if folder is None:
        return
    try:
        record = {
            "run_id": run_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "chapter_id": chapter_id,
            "model": model,
            "ok": error is None,
            "error": None if error is None else f"{type(error).__name__}: {error}",
            "messages": ModelMessagesTypeAdapter.dump_python(list(messages), mode="json"),
        }
        line = json.dumps(record, ensure_ascii=False)
        folder.mkdir(parents=True, exist_ok=True)
        with _lock, open(folder / f"{stage}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as err:  # the log is a debugging aid; never fail the call because of it
        logger.warning("Could not write the call log for %s: %s", run_id, err)
