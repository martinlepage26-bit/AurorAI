"""Planning stubs for the AurorAI D1/R2 migration.

These helpers do not write to D1 or R2 yet.

They provide endpoint-shaped write plans so we can validate:

- record shape
- recursive lineage
- claim-boundary rules
- object-key layout

before switching runtime persistence away from Mongo and local disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class R2ObjectPlan:
    bucket: str
    object_key: str
    content_type: str
    purpose: str


@dataclass
class D1InsertPlan:
    table: str
    values: Dict[str, Any]


@dataclass
class D1UpdatePlan:
    table: str
    key: Dict[str, Any]
    values: Dict[str, Any]


@dataclass
class MigrationWritePlan:
    endpoint: str
    r2_objects: List[R2ObjectPlan] = field(default_factory=list)
    d1_inserts: List[D1InsertPlan] = field(default_factory=list)
    d1_updates: List[D1UpdatePlan] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class AurorAIMigrationPlanner:
    """Generate D1/R2 write plans for AurorAI endpoint migration."""

    artifact_bucket = "govern-artifacts"
    evidence_bucket = "govern-evidence"

    def plan_upload(
        self,
        *,
        artifact_id: str,
        original_filename: str,
        source_extension: str,
        source_hash: str,
        mime_type: str,
        file_size_bytes: int,
        page_count: int,
        source_kind: str = "uploaded_file",
        client_id: Optional[str] = None,
        include_text_object: bool = True,
    ) -> MigrationWritePlan:
        """Stub for `POST /api/documents/upload`."""
        now = iso_now()
        version_id = _new_id("artifact_version")
        source_key = f"artifacts/{artifact_id}/v1/source.{source_extension}"
        text_key = f"artifacts/{artifact_id}/v1/text.txt"

        plan = MigrationWritePlan(endpoint="POST /api/documents/upload")
        plan.r2_objects.append(
            R2ObjectPlan(
                bucket=self.artifact_bucket,
                object_key=source_key,
                content_type=mime_type,
                purpose="source_artifact",
            )
        )
        if include_text_object:
            plan.r2_objects.append(
                R2ObjectPlan(
                    bucket=self.artifact_bucket,
                    object_key=text_key,
                    content_type="text/plain; charset=utf-8",
                    purpose="derived_text",
                )
            )

        plan.d1_inserts.extend(
            [
                D1InsertPlan(
                    table="artifacts",
                    values={
                        "id": artifact_id,
                        "client_id": client_id,
                        "source_kind": source_kind,
                        "original_filename": original_filename,
                        "mime_type": mime_type,
                        "latest_version_no": 1,
                        "current_state": "ingested",
                        "current_review_state": "pending",
                        "created_at": now,
                        "updated_at": now,
                    },
                ),
                D1InsertPlan(
                    table="artifact_versions",
                    values={
                        "id": version_id,
                        "artifact_id": artifact_id,
                        "version_no": 1,
                        "source_object_key": source_key,
                        "derived_text_object_key": text_key if include_text_object else None,
                        "sha256": source_hash,
                        "file_size_bytes": file_size_bytes,
                        "page_count": page_count,
                        "created_at": now,
                    },
                ),
                D1InsertPlan(
                    table="audit_events",
                    values={
                        "id": _new_id("audit"),
                        "aggregate_type": "artifact",
                        "aggregate_id": artifact_id,
                        "event_type": "artifact_uploaded",
                        "actor_type": "system",
                        "event_payload_json": {
                            "artifact_version_id": version_id,
                            "source_hash": source_hash,
                            "source_object_key": source_key,
                        },
                        "created_at": now,
                    },
                ),
            ]
        )
        plan.notes.append("Artifact remains `ingested` until downstream runs produce reviewable evidence.")
        return plan

    def plan_classification(
        self,
        *,
        artifact_id: str,
        artifact_version_id: str,
        category: str,
        document_type: str,
        confidence: float,
        triggered_by: str,
        iteration_index: int,
        parent_run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
    ) -> MigrationWritePlan:
        """Stub for `POST /api/documents/{doc_id}/categorize`."""
        now = iso_now()
        run_id = _new_id("run")
        review_required = confidence < 0.8

        plan = MigrationWritePlan(endpoint="POST /api/documents/{doc_id}/categorize")
        plan.d1_inserts.append(
            D1InsertPlan(
                table="processing_runs",
                values={
                    "id": run_id,
                    "artifact_id": artifact_id,
                    "artifact_version_id": artifact_version_id,
                    "parent_run_id": parent_run_id,
                    "cycle_id": cycle_id,
                    "stage": "classification",
                    "iteration_index": iteration_index,
                    "triggered_by": triggered_by,
                    "status": "complete",
                    "started_at": now,
                    "completed_at": now,
                },
            )
        )
        plan.d1_inserts.append(
            D1InsertPlan(
                table="control_checks",
                values={
                    "id": _new_id("check"),
                    "run_id": run_id,
                    "check_type": "classification_confidence",
                    "status": "review_required" if review_required else "pass",
                    "finding_code": "low_classification_confidence" if review_required else None,
                    "finding_detail_json": {"confidence": confidence, "category": category, "document_type": document_type},
                    "triggered_human_review": 1 if review_required else 0,
                    "created_at": now,
                },
            )
        )
        plan.d1_updates.append(
            D1UpdatePlan(
                table="artifacts",
                key={"id": artifact_id},
                values={
                    "category": category,
                    "document_type": document_type,
                    "latest_run_id": run_id,
                    "current_review_state": "needs_review" if review_required else "pending",
                    "updated_at": now,
                },
            )
        )
        plan.d1_inserts.append(
            D1InsertPlan(
                table="audit_events",
                values={
                    "id": _new_id("audit"),
                    "aggregate_type": "artifact",
                    "aggregate_id": artifact_id,
                    "event_type": "classification_completed",
                    "actor_type": "system",
                    "related_run_id": run_id,
                    "event_payload_json": {
                        "category": category,
                        "document_type": document_type,
                        "confidence": confidence,
                        "review_required": review_required,
                    },
                    "created_at": now,
                },
            )
        )
        plan.notes.append("Low confidence keeps categorization provisional rather than promoting a stronger state.")
        return plan

    def plan_extraction(
        self,
        *,
        artifact_id: str,
        artifact_version_id: str,
        triggered_by: str,
        iteration_index: int,
        fields: List[Dict[str, Any]],
        checks: List[Dict[str, Any]],
        parent_run_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
    ) -> MigrationWritePlan:
        """Stub for `POST /api/documents/{doc_id}/extract`."""
        now = iso_now()
        run_id = _new_id("run")
        unresolved = any(check.get("status") not in {"pass", "complete"} for check in checks)

        plan = MigrationWritePlan(endpoint="POST /api/documents/{doc_id}/extract")
        plan.d1_inserts.append(
            D1InsertPlan(
                table="processing_runs",
                values={
                    "id": run_id,
                    "artifact_id": artifact_id,
                    "artifact_version_id": artifact_version_id,
                    "parent_run_id": parent_run_id,
                    "cycle_id": cycle_id,
                    "stage": "extraction",
                    "iteration_index": iteration_index,
                    "triggered_by": triggered_by,
                    "status": "complete",
                    "started_at": now,
                    "completed_at": now,
                },
            )
        )
        for field in fields:
            plan.d1_inserts.append(
                D1InsertPlan(
                    table="extraction_fields",
                    values={
                        "id": _new_id("field"),
                        "run_id": run_id,
                        "field_name": field["field_name"],
                        "field_value_json": field.get("field_value_json"),
                        "normalized_value": field.get("normalized_value"),
                        "confidence": field.get("confidence"),
                        "is_mandatory": 1 if field.get("is_mandatory") else 0,
                        "below_threshold": 1 if field.get("below_threshold") else 0,
                        "pii_flag": 1 if field.get("pii_flag") else 0,
                        "anomaly_flag": 1 if field.get("anomaly_flag") else 0,
                    },
                )
            )
        for check in checks:
            plan.d1_inserts.append(
                D1InsertPlan(
                    table="control_checks",
                    values={
                        "id": _new_id("check"),
                        "run_id": run_id,
                        "check_type": check["check_type"],
                        "status": check["status"],
                        "finding_code": check.get("finding_code"),
                        "finding_detail_json": check.get("finding_detail_json"),
                        "triggered_human_review": 1 if check.get("triggered_human_review") else 0,
                        "created_at": now,
                    },
                )
            )

        plan.d1_updates.append(
            D1UpdatePlan(
                table="artifacts",
                key={"id": artifact_id},
                values={
                    "latest_run_id": run_id,
                    "current_state": "extracted",
                    "current_review_state": "needs_review" if unresolved else "review_pending_confirmation",
                    "updated_at": now,
                },
            )
        )
        plan.d1_inserts.append(
            D1InsertPlan(
                table="audit_events",
                values={
                    "id": _new_id("audit"),
                    "aggregate_type": "artifact",
                    "aggregate_id": artifact_id,
                    "event_type": "extraction_completed",
                    "actor_type": "system",
                    "related_run_id": run_id,
                    "event_payload_json": {
                        "field_count": len(fields),
                        "check_count": len(checks),
                        "unresolved": unresolved,
                    },
                    "created_at": now,
                },
            )
        )
        plan.notes.append("Extraction completion does not imply review clearance when mandatory checks remain unresolved.")
        return plan

    def plan_evidence_package(
        self,
        *,
        artifact_id: str,
        run_id: str,
        use_case_id: str,
        producer: str,
        artifact_type: str,
        schema_version: str,
        package_hash: str,
        supersedes_package_id: Optional[str] = None,
    ) -> MigrationWritePlan:
        """Stub for `POST /api/documents/{doc_id}/evidence-package`."""
        now = iso_now()
        package_id = _new_id("package")
        payload_key = f"evidence/{use_case_id}/{package_id}.json"

        plan = MigrationWritePlan(endpoint="POST /api/documents/{doc_id}/evidence-package")
        plan.r2_objects.append(
            R2ObjectPlan(
                bucket=self.evidence_bucket,
                object_key=payload_key,
                content_type="application/json",
                purpose="evidence_package_payload",
            )
        )
        plan.d1_inserts.extend(
            [
                D1InsertPlan(
                    table="evidence_packages",
                    values={
                        "id": package_id,
                        "artifact_id": artifact_id,
                        "run_id": run_id,
                        "use_case_id": use_case_id,
                        "schema_version": schema_version,
                        "producer": producer,
                        "artifact_type": artifact_type,
                        "payload_object_key": payload_key,
                        "package_hash": package_hash,
                        "supersedes_package_id": supersedes_package_id,
                        "created_at": now,
                    },
                ),
                D1InsertPlan(
                    table="audit_events",
                    values={
                        "id": _new_id("audit"),
                        "aggregate_type": "artifact",
                        "aggregate_id": artifact_id,
                        "event_type": "evidence_package_created",
                        "actor_type": "system",
                        "related_run_id": run_id,
                        "related_package_id": package_id,
                        "event_payload_json": {"use_case_id": use_case_id, "schema_version": schema_version},
                        "created_at": now,
                    },
                ),
            ]
        )
        plan.d1_updates.append(
            D1UpdatePlan(
                table="artifacts",
                key={"id": artifact_id},
                values={"latest_package_id": package_id, "updated_at": now},
            )
        )
        plan.notes.append("Package creation is append-only and does not imply governance acceptance.")
        return plan

    def plan_handoff(
        self,
        *,
        artifact_id: str,
        use_case_id: str,
        package_id: str,
        target_url: str,
        status_code: int,
        payload_changed: bool = False,
    ) -> MigrationWritePlan:
        """Stub for `POST /api/documents/{doc_id}/handoff-to-compassai`."""
        now = iso_now()
        success = status_code < 400

        plan = MigrationWritePlan(endpoint="POST /api/documents/{doc_id}/handoff-to-compassai")
        plan.d1_inserts.extend(
            [
                D1InsertPlan(
                    table="use_case_evidence_links",
                    values={
                        "id": _new_id("link"),
                        "use_case_id": use_case_id,
                        "evidence_package_id": package_id,
                        "relation_type": "primary",
                        "linked_at": now,
                    },
                ),
                D1InsertPlan(
                    table="audit_events",
                    values={
                        "id": _new_id("audit"),
                        "aggregate_type": "artifact",
                        "aggregate_id": artifact_id,
                        "event_type": "evidence_handoff_succeeded" if success else "evidence_handoff_failed",
                        "actor_type": "system",
                        "related_package_id": package_id,
                        "event_payload_json": {
                            "use_case_id": use_case_id,
                            "target_url": target_url,
                            "status_code": status_code,
                            "payload_changed": payload_changed,
                        },
                        "created_at": now,
                    },
                ),
            ]
        )
        plan.d1_updates.append(
            D1UpdatePlan(
                table="artifacts",
                key={"id": artifact_id},
                values={
                    "current_state": "handed_off_to_compassai" if success else "handoff_failed",
                    "updated_at": now,
                },
            )
        )
        plan.notes.append("Successful handoff means delivery to CompassAI, not governance approval.")
        return plan
