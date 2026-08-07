"""Typed persistence for case state, evidence, review, decisions, and results."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from invoice_agents.db.core import connect_database
from invoice_agents.errors import ErrorCategory, InvoiceAgentsError
from invoice_agents.models import (
    CaseResult,
    CaseStatus,
    Critique,
    ExtractedInvoice,
    FinalDecision,
    HumanDecision,
    ReviewRequest,
    SourceArtifact,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)


class WorkflowStore:
    """Own all mutation of the workflow database; inventory remains separate."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def register_source(self, source: SourceArtifact) -> None:
        with connect_database(self.path) as connection:
            connection.execute(
                "INSERT INTO source_artifacts("
                "source_id, canonical_path, source_hash, source_format, size_bytes, modified_at, "
                "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET metadata_json=excluded.metadata_json",
                (
                    source.source_id,
                    str(source.canonical_path),
                    source.sha256,
                    source.source_format,
                    source.size_bytes,
                    source.modified_at.isoformat(),
                    source.model_dump_json(),
                    now_iso(),
                ),
            )
            connection.commit()

    def create_case(self, case_id: str, source: SourceArtifact, started_at: datetime) -> None:
        with connect_database(self.path) as connection:
            connection.execute(
                "INSERT INTO cases(case_id, source_id, status, stop_reason, started_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    source.source_id,
                    CaseStatus.INCOMPLETE,
                    "CASE_CREATED",
                    started_at.isoformat(),
                    now_iso(),
                ),
            )
            connection.commit()

    def save_extraction(self, case_id: str, invoice: ExtractedInvoice) -> str:
        extraction_id = f"ext_{uuid4().hex}"
        with connect_database(self.path) as connection:
            version_row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM extractions WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            version = int(version_row["version"]) + 1
            connection.execute(
                "INSERT INTO extractions(extraction_id, case_id, version, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (extraction_id, case_id, version, invoice.model_dump_json(), now_iso()),
            )
            connection.execute(
                "UPDATE cases SET invoice_number = ?, vendor = ?, revision = ?, updated_at = ? "
                "WHERE case_id = ?",
                (
                    invoice.invoice_number.normalized_value,
                    invoice.vendor.normalized_value,
                    invoice.revision.normalized_value if invoice.revision else None,
                    now_iso(),
                    case_id,
                ),
            )
            connection.commit()
        return extraction_id

    def load_extraction(self, case_id: str) -> ExtractedInvoice:
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT payload_json FROM extractions WHERE case_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        if row is None:
            raise InvoiceAgentsError(
                ErrorCategory.DATABASE,
                f"no extraction exists for case {case_id}",
                case_id=case_id,
                stop_reason="EXTRACTION_NOT_FOUND",
            )
        return ExtractedInvoice.model_validate_json(row["payload_json"])

    def save_identity(self, case_id: str, payload: list[dict[str, Any]]) -> str:
        identity_id = f"ident_{uuid4().hex}"
        self._insert_payload("identity_results", "identity_id", identity_id, case_id, payload)
        return identity_id

    def load_identity(self, case_id: str) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._load_latest_payload("identity_results", case_id))

    def save_comparison(self, case_id: str, kind: str, payload: Any) -> str:
        comparison_id = f"cmp_{uuid4().hex}"
        with connect_database(self.path) as connection:
            connection.execute(
                "INSERT INTO comparison_results("
                "comparison_id, case_id, comparison_type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (comparison_id, case_id, kind, encode(payload), now_iso()),
            )
            connection.commit()
        return comparison_id

    def load_comparison(self, case_id: str, kind: str) -> Any:
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT payload_json FROM comparison_results "
                "WHERE case_id = ? AND comparison_type = ? ORDER BY created_at DESC LIMIT 1",
                (case_id, kind),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_critique(self, case_id: str, critique: Critique) -> str:
        critique_id = f"crit_{uuid4().hex}"
        self._insert_payload("critique_results", "critique_id", critique_id, case_id, critique)
        return critique_id

    def load_critique(self, case_id: str) -> Critique:
        payload = self._load_latest_payload("critique_results", case_id)
        if not payload:
            raise InvoiceAgentsError(
                ErrorCategory.DATABASE,
                f"critic has not recorded a result for case {case_id}",
                case_id=case_id,
                stop_reason="CRITIQUE_MISSING",
            )
        return Critique.model_validate(payload)

    def save_review(self, review: ReviewRequest) -> ReviewRequest:
        """Persist the next review cycle for the case and return it with its sequence.

        The UNIQUE(case_id, sequence) index turns a concurrent double-insert into a
        visible IntegrityError instead of a silently reordered queue.
        """

        with connect_database(self.path) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM review_requests "
                "WHERE case_id = ?",
                (review.case_id,),
            ).fetchone()
            sequenced = review.model_copy(update={"sequence": int(row["sequence"]) + 1}, deep=True)
            connection.execute(
                "INSERT INTO review_requests("
                "review_id, case_id, sequence, status, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    sequenced.review_id,
                    sequenced.case_id,
                    sequenced.sequence,
                    sequenced.status,
                    sequenced.model_dump_json(),
                    sequenced.created_at.isoformat(),
                ),
            )
            connection.commit()
        return sequenced

    def load_review(self, review_id: str) -> ReviewRequest:
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT payload_json FROM review_requests WHERE review_id = ?", (review_id,)
            ).fetchone()
        if row is None:
            raise InvoiceAgentsError(
                ErrorCategory.DATABASE,
                f"review request does not exist: {review_id}",
                stop_reason="REVIEW_NOT_FOUND",
            )
        return ReviewRequest.model_validate_json(row["payload_json"])

    def load_case_review(self, case_id: str) -> ReviewRequest | None:
        """Return the latest review cycle for the case, by sequence."""

        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT payload_json FROM review_requests WHERE case_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        return ReviewRequest.model_validate_json(row["payload_json"]) if row else None

    def list_reviews(self, pending_only: bool = True) -> list[ReviewRequest]:
        sql = "SELECT payload_json FROM review_requests"
        params: tuple[str, ...] = ()
        if pending_only:
            sql += " WHERE status = ?"
            params = ("PENDING",)
        sql += " ORDER BY created_at, sequence"
        with connect_database(self.path, read_only=True) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [ReviewRequest.model_validate_json(row["payload_json"]) for row in rows]

    def save_human_decision(self, decision: HumanDecision) -> ReviewRequest:
        review = self.load_review(decision.review_id)
        if review.status == "RESOLVED":
            existing = review.human_decision
            if existing and existing.model_dump() == decision.model_dump():
                return review
            raise InvoiceAgentsError(
                ErrorCategory.DATABASE,
                f"review {decision.review_id} is already resolved",
                case_id=review.case_id,
                stop_reason="REVIEW_ALREADY_RESOLVED",
            )
        resolved = review.model_copy(
            update={"status": "RESOLVED", "human_decision": decision}, deep=True
        )
        with connect_database(self.path) as connection:
            try:
                connection.execute(
                    "INSERT INTO human_decisions("
                    "decision_id, review_id, reviewer, decision, reason, payload_json, decided_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"hdec_{uuid4().hex}",
                        decision.review_id,
                        decision.reviewer,
                        decision.decision,
                        decision.reason,
                        decision.model_dump_json(),
                        decision.decided_at.isoformat(),
                    ),
                )
                connection.execute(
                    "UPDATE review_requests SET status = 'RESOLVED', payload_json = ?, "
                    "resolved_at = ? WHERE review_id = ?",
                    (
                        resolved.model_dump_json(),
                        decision.decided_at.isoformat(),
                        decision.review_id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return resolved

    def save_final_decision(self, case_id: str, decision: FinalDecision) -> None:
        with connect_database(self.path) as connection:
            connection.execute(
                "INSERT INTO final_decisions(decision_id, case_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET "
                "payload_json=excluded.payload_json, created_at=excluded.created_at",
                (f"fdec_{uuid4().hex}", case_id, decision.model_dump_json(), now_iso()),
            )
            connection.commit()

    def load_final_decision(self, case_id: str) -> FinalDecision | None:
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT payload_json FROM final_decisions WHERE case_id = ?", (case_id,)
            ).fetchone()
        return FinalDecision.model_validate_json(row["payload_json"]) if row else None

    def save_team_state(self, case_id: str, state: dict[str, Any]) -> None:
        with connect_database(self.path) as connection:
            connection.execute(
                "UPDATE cases SET team_state_json = ?, updated_at = ? WHERE case_id = ?",
                (encode(state), now_iso(), case_id),
            )
            connection.commit()

    def load_team_state(self, case_id: str) -> dict[str, Any] | None:
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT team_state_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise InvoiceAgentsError(
                ErrorCategory.DATABASE,
                f"case does not exist: {case_id}",
                case_id=case_id,
                stop_reason="CASE_NOT_FOUND",
            )
        return json.loads(row["team_state_json"]) if row["team_state_json"] else None

    def finish_case(self, result: CaseResult) -> None:
        with connect_database(self.path) as connection:
            connection.execute(
                "UPDATE cases SET status = ?, stop_reason = ?, result_json = ?, updated_at = ?, "
                "finished_at = ? WHERE case_id = ?",
                (
                    result.status,
                    result.stop_reason,
                    result.model_dump_json(),
                    now_iso(),
                    result.finished_at.isoformat(),
                    result.case_id,
                ),
            )
            connection.commit()

    def load_result(self, case_id: str) -> CaseResult | None:
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT result_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise InvoiceAgentsError(
                ErrorCategory.DATABASE,
                f"case does not exist: {case_id}",
                case_id=case_id,
                stop_reason="CASE_NOT_FOUND",
            )
        return CaseResult.model_validate_json(row["result_json"]) if row["result_json"] else None

    def count_events(self, case_id: str, event_type: str) -> int:
        """Count persisted audit events of one type; retries use 'provider.retry'."""

        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS event_count FROM events WHERE case_id = ? AND event_type = ?",
                (case_id, event_type),
            ).fetchone()
        return int(row["event_count"])

    def identity_rows(
        self, case_id: str, invoice_number: str | None, vendor: str | None
    ) -> list[Any]:
        with connect_database(self.path, read_only=True) as connection:
            return connection.execute(
                "SELECT c.case_id, c.invoice_number, c.vendor, c.revision, "
                "s.source_id, s.source_hash, s.source_format "
                "FROM cases c JOIN source_artifacts s ON s.source_id = c.source_id "
                "WHERE c.case_id <> ? AND (c.invoice_number = ? OR c.vendor = ?)",
                (case_id, invoice_number, vendor),
            ).fetchall()

    def _insert_payload(
        self,
        table: str,
        id_column: str,
        record_id: str,
        case_id: str,
        payload: Any,
    ) -> None:
        allowed = {"identity_results", "critique_results"}
        if table not in allowed:
            raise ValueError(f"unsupported payload table: {table}")
        with connect_database(self.path) as connection:
            connection.execute(
                f"INSERT INTO {table}({id_column}, case_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (record_id, case_id, encode(payload), now_iso()),
            )
            connection.commit()

    def _load_latest_payload(self, table: str, case_id: str) -> Any:
        allowed = {"identity_results", "critique_results"}
        if table not in allowed:
            raise ValueError(f"unsupported payload table: {table}")
        with connect_database(self.path, read_only=True) as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE case_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else []
