"""Server-sent event tail over persisted case events.

The stream is only a window onto the events table: rows are read with a rowid
cursor at ~1s cadence and forwarded verbatim (summarized for display, payload
untouched). Terminal state is emitted from the stored case row, never inferred.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sse_starlette import ServerSentEvent

from invoice_agents.ui.queries import EventRow, case_header, events_after
from invoice_agents.ui.runs import RunRegistry

POLL_SECONDS = 1.0


def _tool_names(content: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                calls.append(
                    {
                        "id": item.get("id") or item.get("call_id"),
                        "name": item.get("name"),
                        "is_error": item.get("is_error"),
                    }
                )
    return calls


def summarize_event(row: EventRow) -> dict[str, Any]:
    """Extract display fields for the live timeline from one stored event."""

    try:
        payload = json.loads(row.payload_json)
    except json.JSONDecodeError:
        payload = None
    summary: dict[str, Any] = {
        "seq": row.seq,
        "event_type": row.event_type,
        "agent": row.agent_name,
        "created_at": row.created_at,
    }
    if not isinstance(payload, dict):
        return summary
    if row.event_type == "autogen.HandoffMessage":
        summary["handoff"] = {"source": payload.get("source"), "target": payload.get("target")}
    elif row.event_type == "autogen.ToolCallRequestEvent":
        summary["tool_calls"] = _tool_names(payload.get("content"))
    elif row.event_type == "autogen.ToolCallExecutionEvent":
        summary["tool_results"] = _tool_names(payload.get("content"))
    elif row.event_type == "provider.retry":
        summary["message"] = payload.get("message")
    elif row.event_type in {"case.finished", "case.resumed_finished", "case.failed"}:
        summary["status"] = payload.get("status")
        summary["stop_reason"] = payload.get("stop_reason")
    return summary


def terminal_payload(
    workflow_db: Path, case_id: str, registry: RunRegistry
) -> dict[str, Any] | None:
    """The stored terminal state for the case, or None while a result is pending."""

    if registry.is_running(case_id):
        return None
    header = case_header(workflow_db, case_id)
    if header is None:
        return {"case_id": case_id, "missing": True}
    run_error = registry.run_error(case_id)
    if not header.has_result and run_error is None:
        return None
    return {
        "case_id": case_id,
        "status": header.status,
        "stop_reason": header.stop_reason,
        "run_error": run_error,
    }


async def case_event_stream(
    workflow_db: Path,
    case_id: str,
    registry: RunRegistry,
    after_seq: int = 0,
) -> AsyncIterator[ServerSentEvent]:
    """Yield stored events as they appear, then one terminal event, then stop."""

    last_seq = after_seq
    while True:
        rows = await asyncio.to_thread(events_after, workflow_db, case_id, last_seq)
        for row in rows:
            last_seq = row.seq
            yield ServerSentEvent(
                event="case-event",
                id=str(row.seq),
                data=json.dumps(summarize_event(row), ensure_ascii=False, default=str),
            )
        terminal = await asyncio.to_thread(terminal_payload, workflow_db, case_id, registry)
        if terminal is not None:
            yield ServerSentEvent(
                event="terminal",
                data=json.dumps(terminal, ensure_ascii=False, default=str),
            )
            return
        await asyncio.sleep(POLL_SECONDS)
