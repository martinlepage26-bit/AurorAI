# AurorAI InfraFabric Alignment v2

## What changed in this pass

This pass aligns AurorAI more closely with the COM-AUR v3 structure and the InfraFabric processing model.

It does not change AurorAI into a full D1/R2 implementation yet.

It does make the recursive path visible inside the current Mongo-backed document model.

## New first-class state now tracked

- `processing_runs`
- `review_decisions`
- `package_history`
- `handoff_history`
- `current_state`
- `current_review_state`
- `latest_run_id`
- `latest_package_id`
- `latest_handoff_id`

## Why this matters

AurorAI was previously carrying most of its state in:

- `audit_log`
- `extraction`
- `control_checks`
- `review_required`

That was enough for a functional product, but not enough for a faithful InfraFabric-style recursive record.

The new structure makes these distinctions explicit:

1. a processing pass
2. a review decision on that pass
3. an evidence package produced from that pass
4. a handoff outcome produced from that package

## Stage mapping

| AurorAI route | InfraFabric-aligned state now produced |
|---|---|
| `POST /api/documents/upload` | source hash + upload audit event + initial state |
| `POST /api/documents/{doc_id}/categorize` | classification processing run + optional HITL review decision |
| `POST /api/documents/bulk-categorize` | per-document classification run + optional HITL review decision |
| `POST /api/documents/{doc_id}/summary` | summary processing run |
| `POST /api/documents/{doc_id}/extract` | extraction processing run + auto-accept or HITL escalation decision |
| `POST /api/documents/{doc_id}/evidence-package` | append-only package history + latest package pointer |
| `POST /api/documents/{doc_id}/handoff-to-compassai` | handoff history with success/failure state |
| `POST /api/documents/{doc_id}/citations` | citation-extraction processing run |

## What is still not finished

- no separate `artifact_versions` collection yet
- no dedicated `processing_runs` collection yet
- no `if.trace` receipt issuance yet
- no D1 or R2 persistence yet
- no explicit HITL reviewer endpoint yet

## Design rule preserved

A new pass should create a new record.

It should not silently overwrite the prior pass and pretend the prior pass never existed.
