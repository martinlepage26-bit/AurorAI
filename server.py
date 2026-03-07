from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Form, Depends
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import hashlib
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
import uuid
from datetime import datetime, timezone
import fitz  # PyMuPDF
import aiofiles
import tempfile
import json
import re
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create uploads directory
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")
security = HTTPBearer(auto_error=False)
AURORAI_API_TOKEN = os.environ.get("AURORAI_API_TOKEN", "")
COMPASSAI_BASE_URL = os.environ.get("COMPASSAI_BASE_URL", "http://127.0.0.1:9205").rstrip("/")
COMPASSAI_INGEST_TOKEN = os.environ.get("COMPASSAI_INGEST_TOKEN", "")

# Categories
CATEGORIES = [
    "Academic Papers",
    "Invoices/Receipts",
    "Contracts",
    "Reports",
    "Personal Documents",
    "Resumes",
    "My Writings & Publications",
    "Uncategorized"
]

IDP_MISSION = (
    "AurorAI transforme des documents bloqués en données structurées, "
    "recherchables et exploitables, avec traçabilité, sécurité et conformité."
)

IDP_PIPELINE = [
    "Ingestion: upload / email / dépôt / API",
    "Lecture (OCR): extraction de texte depuis scans, images, PDF",
    "Compréhension (NLP): contexte, entités, relations",
    "Classification: type de document + sous-type",
    "Extraction: champs clés vers un schéma JSON/CSV avec score de confiance",
    "Contrôle (HITL): validation humaine ciblée sous seuil de confiance",
    "Routage & intégration: ERP/CRM/DMS, workflows et alertes",
    "Gouvernance: audit trail, versioning, rétention et contrôle d'accès",
]

# Define Models
class Document(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    original_filename: str
    category: str = "Uncategorized"
    ai_suggested_category: Optional[str] = None
    document_type: Optional[str] = None
    classification_confidence: Optional[float] = None
    classification_rationale: Optional[str] = None
    file_size: int = 0
    page_count: int = 0
    text_preview: str = ""
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_academic: bool = False
    citations: List[str] = []
    summary: Optional[str] = None
    extraction: Dict[str, Any] = Field(default_factory=dict)
    control_checks: Dict[str, List[str]] = Field(default_factory=dict)
    review_required: bool = False
    compliance_note: Optional[str] = None
    source_hash: Optional[str] = None
    audit_log: List[Dict[str, Any]] = Field(default_factory=list)
    current_state: str = "uploaded"
    current_review_state: str = "pending"
    latest_run_id: Optional[str] = None
    latest_package_id: Optional[str] = None
    latest_handoff_id: Optional[str] = None
    processing_runs: List[Dict[str, Any]] = Field(default_factory=list)
    review_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    package_history: List[Dict[str, Any]] = Field(default_factory=list)
    handoff_history: List[Dict[str, Any]] = Field(default_factory=list)
    reading_list_id: Optional[str] = None
    tags: List[str] = []

class DocumentCreate(BaseModel):
    filename: str
    original_filename: str
    category: str = "Uncategorized"
    file_size: int = 0
    page_count: int = 0
    text_preview: str = ""

class DocumentUpdate(BaseModel):
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    reading_list_id: Optional[str] = None

class ReadingList(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    document_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ReadingListCreate(BaseModel):
    name: str
    description: str = ""

class CategoryStats(BaseModel):
    category: str
    count: int
    percentage: float

class AICategorizationRequest(BaseModel):
    document_id: str

class AISummaryRequest(BaseModel):
    document_id: str

class CitationExtractionRequest(BaseModel):
    document_id: str


class EvidencePackageRequest(BaseModel):
    usecase_id: str
    producer: str = "aurorai"
    artifact_type: str = "evidence_package"


class EvidenceHandoffRequest(EvidencePackageRequest):
    compassai_base_url: Optional[str] = None


async def require_api_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Protect sensitive document routes until shared auth is introduced."""
    if not AURORAI_API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="AURORAI_API_TOKEN is not configured on the server.",
        )

    if not credentials or credentials.credentials != AURORAI_API_TOKEN:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return True


def append_audit_event(document: Dict[str, Any], action: str, details: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = document.get("audit_log", [])
    events.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "details": details,
        }
    )
    return events[-100:]


def latest_stage_run_id(document: Dict[str, Any], stage: str) -> Optional[str]:
    for run in reversed(document.get("processing_runs", [])):
        if run.get("stage") == stage:
            return run.get("id")
    return None


def append_processing_run(
    document: Dict[str, Any],
    stage: str,
    *,
    triggered_by: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    runs = list(document.get("processing_runs", []))
    stage_runs = [run for run in runs if run.get("stage") == stage]
    run_doc = {
        "id": str(uuid.uuid4()),
        "stage": stage,
        "status": status,
        "triggered_by": triggered_by,
        "parent_run_id": latest_stage_run_id(document, stage),
        "iteration_index": len(stage_runs) + 1,
        "started_at": iso_now(),
        "completed_at": iso_now(),
        "details": details or {},
    }
    runs.append(run_doc)
    return runs[-150:], run_doc


def append_review_decision(
    document: Dict[str, Any],
    *,
    run_id: Optional[str],
    decision_type: str,
    rationale: str,
    resulting_state: str,
    actor_type: str = "system",
    actor_id: str = "aurorai",
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    decisions = list(document.get("review_decisions", []))
    decision_doc = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "parent_decision_id": decisions[-1]["id"] if decisions else None,
        "review_round": len(decisions) + 1,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "decision_type": decision_type,
        "rationale": rationale,
        "resulting_state": resulting_state,
        "created_at": iso_now(),
    }
    decisions.append(decision_doc)
    return decisions[-150:], decision_doc


def append_package_history(
    document: Dict[str, Any],
    *,
    evidence_id: str,
    version: int,
    usecase_id: str,
    package_hash: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    history = list(document.get("package_history", []))
    package_doc = {
        "id": evidence_id,
        "version": version,
        "usecase_id": usecase_id,
        "hash": package_hash,
        "created_at": iso_now(),
        "supersedes_package_id": history[-1]["id"] if history else None,
    }
    history.append(package_doc)
    return history[-100:], package_doc


def append_handoff_history(
    document: Dict[str, Any],
    *,
    evidence_id: str,
    target: str,
    target_url: str,
    status_code: int,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    history = list(document.get("handoff_history", []))
    handoff_doc = {
        "id": str(uuid.uuid4()),
        "evidence_id": evidence_id,
        "target": target,
        "target_url": target_url,
        "status_code": status_code,
        "status": "succeeded" if status_code < 400 else "failed",
        "created_at": iso_now(),
    }
    history.append(handoff_doc)
    return history[-100:], handoff_doc


def parse_json_response(raw: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def run_control_checks(extraction_fields: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    missing = []
    anomalies = []
    invalid = []
    duplicates = []

    values_seen = {}
    for field_name, field_data in extraction_fields.items():
        value = str(field_data.get("value", "")).strip()
        if not value:
            missing.append(field_name)
            continue

        if value in values_seen:
            duplicates.append(f"{field_name} duplicates {values_seen[value]}")
        else:
            values_seen[value] = field_name

        lower_field = field_name.lower()
        if "date" in lower_field and not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}", value):
            invalid.append(f"{field_name} has non-standard date format: {value}")
        if any(token in lower_field for token in ["amount", "total", "price"]) and not re.search(r"\d", value):
            invalid.append(f"{field_name} has no numeric value: {value}")
        if "id" in lower_field or "number" in lower_field:
            if len(value) < 4:
                anomalies.append(f"{field_name} looks too short: {value}")

    return {
        "missing_fields": missing,
        "inconsistencies": anomalies,
        "duplicate_values": duplicates,
        "invalid_values": invalid,
    }


def compute_sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def compute_sha256_json(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return compute_sha256_bytes(canonical)


def detect_pii_markers(text: str) -> List[str]:
    markers = []
    pii_patterns = {
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b(?:\d[ -]?){13,16}\b",
        "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "phone": r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b",
        "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
    }

    for label, pattern in pii_patterns.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            markers.append(label)

    lowered = text.lower()
    keyword_markers = {
        "financial_context": ["account number", "routing number", "wire transfer"],
        "medical_context": ["patient", "diagnosis", "medical record", "health card"],
    }
    for label, keywords in keyword_markers.items():
        if any(keyword in lowered for keyword in keywords):
            markers.append(label)

    return sorted(set(markers))


def get_below_threshold_fields(
    extraction_fields: Dict[str, Dict[str, Any]],
    threshold: float = 0.85,
) -> List[Dict[str, Any]]:
    below_threshold = []
    for field_name, field_data in extraction_fields.items():
        confidence = field_data.get("confidence")
        if isinstance(confidence, (int, float)) and float(confidence) < threshold:
            below_threshold.append(
                {
                    "field": field_name,
                    "confidence": round(float(confidence), 4),
                    "threshold": threshold,
                }
            )
    return below_threshold


def detect_high_value_transaction(extraction_fields: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for field_name, field_data in extraction_fields.items():
        if not any(token in field_name.lower() for token in ["amount", "total", "price", "payment"]):
            continue

        raw_value = str(field_data.get("value", "")).strip()
        normalized = re.sub(r"[^0-9.,-]", "", raw_value).replace(",", "")
        if not normalized:
            continue

        try:
            amount = float(normalized)
        except ValueError:
            continue

        if amount >= 10000:
            return {
                "field": field_name,
                "amount": amount,
            }

    return None


def build_hitl_reason(
    below_threshold_fields: List[Dict[str, Any]],
    pii_markers: List[str],
    high_value_trigger: Optional[Dict[str, Any]],
) -> Optional[str]:
    reasons = []
    if below_threshold_fields:
        reasons.append("confidence_below_threshold")
    if pii_markers:
        reasons.append("pii_detected")
    if high_value_trigger:
        reasons.append("high_value_transaction")
    return ", ".join(reasons) if reasons else None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_evidence_package(document: Dict[str, Any], request: EvidencePackageRequest) -> Dict[str, Any]:
    extraction = document.get("extraction", {}) or {}
    fields = extraction.get("fields", {}) if isinstance(extraction.get("fields"), dict) else {}
    controls = document.get("control_checks", {}) or {}

    below_threshold_fields = get_below_threshold_fields(fields)
    pii_markers = detect_pii_markers(document.get("text_preview", ""))
    high_value_trigger = detect_high_value_transaction(fields)
    mandatory_fields_present = not bool(controls.get("missing_fields"))
    hitl_trigger_reason = build_hitl_reason(below_threshold_fields, pii_markers, high_value_trigger)
    hitl_triggered = bool(document.get("review_required") or hitl_trigger_reason)
    validation_status = (
        "awaiting_hitl"
        if hitl_triggered
        else "auto_approved"
        if mandatory_fields_present and not controls.get("invalid_values")
        else "needs_review"
    )

    payload = {
        "evidence_package": {
            "extraction_id": f"EXT-{datetime.now(timezone.utc).year}-{document['id'][:8].upper()}",
            "schema_version": "2026-03-01",
            "document_metadata": {
                "source_hash": document.get("source_hash"),
                "document_type": extraction.get("document_type") or document.get("document_type") or "unknown",
                "processing_timestamp": iso_now(),
                "original_filename": document.get("original_filename"),
            },
            "extraction_results": {
                "fields": fields,
                "mandatory_fields_present": mandatory_fields_present,
                "below_threshold_fields": below_threshold_fields,
            },
            "quality_controls": {
                "pii_detected": pii_markers,
                "pii_masking_applied": False,
                "hitl_triggered": hitl_triggered,
                "hitl_trigger_reason": hitl_trigger_reason,
                "confidence_check": "passed" if not below_threshold_fields else "failed",
                "validation_status": validation_status,
            },
            "metrics": {
                "processing_time_ms": None,
                "model_version": os.environ.get("AURORAI_MODEL_VERSION", "aurorai-preview"),
                "accuracy_score_benchmark": None,
                "benchmark_dataset": None,
                "benchmark_date": None,
            },
            "audit_trail": {
                "processor_id": os.environ.get("AURORAI_PROCESSOR_ID", "aurorai-api"),
                "validation_chain": [event.get("action") for event in document.get("audit_log", []) if event.get("action")],
                "approver": None,
                "override_applied": False,
            },
            "if_trace_receipt": None,
            "if_trace_binding_note": (
                "if.trace binding is available via operator-initiated receipt generation; "
                "not automatic on every package in the current implementation"
            ),
        }
    }

    envelope_control_checks = {
        "missing_fields": controls.get("missing_fields", []),
        "invalid_values": controls.get("invalid_values", []),
        "duplicate_values": controls.get("duplicate_values", []),
        "inconsistencies": controls.get("inconsistencies", []),
        "review_required": hitl_triggered,
        "pii_detected": pii_markers,
        "high_value_trigger": high_value_trigger,
    }

    return {
        "usecase_id": request.usecase_id,
        "producer": request.producer,
        "artifact_type": request.artifact_type,
        "payload": payload,
        "hash": compute_sha256_json(payload),
        "control_checks": envelope_control_checks,
    }


def heuristic_document_type(text: str) -> str:
    lowered = text.lower()
    if "invoice" in lowered or "total amount" in lowered:
        return "invoice"
    if "agreement" in lowered or "contract" in lowered:
        return "contract"
    if "resume" in lowered or "curriculum vitae" in lowered:
        return "resume"
    if "report" in lowered:
        return "report"
    return "text_document"


def heuristic_extract_fields(text: str) -> Dict[str, Any]:
    field_patterns = [
        ("invoice_number", r"(?:invoice(?: number| no\.?| #)?)[\s:.-]*([A-Z0-9-]+)", 0.72),
        ("total_amount", r"(?:total(?: amount)?|amount due)[\s:.-]*([$]?\d[\d,]*(?:\.\d{2})?)", 0.72),
        ("vendor_name", r"(?:vendor|supplier|from)[\s:.-]*([^\n]+)", 0.68),
        ("document_date", r"(?:date)[\s:.-]*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4}|[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})", 0.66),
        ("contact_email", r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", 0.7),
        ("contact_phone", r"((?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4})", 0.67),
    ]

    fields: Dict[str, Dict[str, Any]] = {}
    for field_name, pattern, confidence in field_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        evidence_snippet = match.group(0).strip()[:120]
        fields[field_name] = {
            "value": value,
            "confidence": confidence,
            "evidence": evidence_snippet,
        }

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line and "document_title" not in fields:
        fields["document_title"] = {
            "value": first_line[:120],
            "confidence": 0.58,
            "evidence": first_line[:120],
        }

    return {
        "document_type": heuristic_document_type(text),
        "fields": fields,
        "missing_or_ambiguous": [] if fields else ["No structured fields were identified heuristically"],
        "recommended_next_actions": (
            ["Run HITL validation", "Configure EMERGENT_LLM_KEY for richer extraction"]
            if fields
            else ["Run HITL validation", "Provide a more structured source document"]
        ),
    }


async def post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")

    def _send() -> Dict[str, Any]:
        req = urllib_request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=20) as response:
                raw_body = response.read().decode("utf-8")
                return {
                    "status_code": response.getcode(),
                    "body": json.loads(raw_body) if raw_body else None,
                }
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8")
            return {
                "status_code": exc.code,
                "body": json.loads(raw_body) if raw_body else None,
            }
        except URLError as exc:
            raise HTTPException(status_code=502, detail=f"CompassAI handoff failed: {exc.reason}") from exc

    return await asyncio.to_thread(_send)


async def create_evidence_record(doc_id: str, request: EvidencePackageRequest) -> Dict[str, Any]:
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    extraction = doc.get("extraction", {}) or {}
    fields = extraction.get("fields", {}) if isinstance(extraction.get("fields"), dict) else {}
    if not fields:
        raise HTTPException(
            status_code=400,
            detail="Structured extraction is required before an evidence package can be generated.",
        )

    if not doc.get("source_hash"):
        file_path = UPLOAD_DIR / doc["filename"]
        if file_path.exists():
            with open(file_path, "rb") as handle:
                source_hash = compute_sha256_bytes(handle.read())
            doc["source_hash"] = source_hash
            await db.documents.update_one({"id": doc_id}, {"$set": {"source_hash": source_hash}})
        else:
            raise HTTPException(status_code=400, detail="Source hash is missing and the source file is no longer available.")

    evidence_envelope = build_evidence_package(doc, request)
    version = await db.evidence_packages.count_documents(
        {"document_id": doc_id, "usecase_id": request.usecase_id}
    ) + 1
    evidence_id = str(uuid.uuid4())
    created_at = iso_now()

    evidence_record = {
        "id": evidence_id,
        "document_id": doc_id,
        "version": version,
        "created_at": created_at,
        **evidence_envelope,
    }

    await db.evidence_packages.insert_one(dict(evidence_record))
    package_history, package_doc = append_package_history(
        doc,
        evidence_id=evidence_id,
        version=version,
        usecase_id=request.usecase_id,
        package_hash=evidence_record["hash"],
    )
    updated_audit_log = append_audit_event(
        doc,
        "evidence_package",
        {
            "evidence_id": evidence_id,
            "usecase_id": request.usecase_id,
            "version": version,
        },
    )
    await db.documents.update_one(
        {"id": doc_id},
        {
            "$set": {
                "audit_log": updated_audit_log,
                "package_history": package_history,
                "latest_package_id": package_doc["id"],
                "current_state": "packaged",
            }
        },
    )
    evidence_record["document_audit_log"] = updated_audit_log
    return evidence_record

# Helper function to extract text from PDF
def extract_pdf_text(pdf_path: str, max_pages: int = 10) -> tuple[str, int]:
    """Extract text from PDF file"""
    try:
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        text = ""
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text += page.get_text()
        doc.close()
        return text.strip(), page_count
    except Exception as e:
        logging.error(f"Error extracting PDF text: {e}")
        return "", 0

# Helper function to extract citations
def extract_citations_from_text(text: str) -> List[str]:
    """Extract citations from text using common patterns"""
    citations = []
    # Pattern for common citation formats
    patterns = [
        r'\[(\d+)\]',  # [1], [2], etc.
        r'\(([A-Z][a-z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-z]+)?)?,?\s*\d{4})\)',  # (Author, 2020)
        r'([A-Z][a-z]+(?:\s+(?:et\s+al\.?|&\s+[A-Z][a-z]+)?)\s*\(\d{4}\))',  # Author (2020)
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        citations.extend(matches[:20])  # Limit to 20 citations per pattern
    
    return list(set(citations))[:50]  # Return unique citations, max 50

# AI Integration using Emergent
def get_llm_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY") or ""


def get_llm_base_url() -> Optional[str]:
    return os.environ.get("OPENAI_BASE_URL") or None


def infer_category_from_document_type(document_type: str) -> str:
    mapping = {
        "invoice": "Invoices/Receipts",
        "contract": "Contracts",
        "resume": "Resumes",
        "report": "Reports",
        "text_document": "Uncategorized",
        "unknown": "Uncategorized",
    }
    return mapping.get((document_type or "unknown").lower(), "Uncategorized")


async def run_llm_prompt(system_message: str, prompt: str, model: str = "gpt-4o-mini") -> str:
    from emergentintegrations.llm.chat import LlmChat, SystemMessage, UserMessage

    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("No OpenAI-compatible LLM key configured.")

    chat = LlmChat(
        model=model,
        api_key=api_key,
        base_url=get_llm_base_url(),
    )
    return await asyncio.to_thread(
        chat.chat,
        [
            SystemMessage(content=system_message),
            UserMessage(content=prompt),
        ],
    )


async def ai_categorize_document(text: str) -> Dict[str, Any]:
    """Use AI to categorize document based on content."""
    try:
        if not get_llm_api_key():
            document_type = heuristic_document_type(text)
            return {
                "category": infer_category_from_document_type(document_type),
                "document_type": document_type,
                "confidence": 0.42 if document_type != "unknown" else 0.0,
                "rationale": "No LLM key configured. Heuristic categorization was used.",
            }

        response = await run_llm_prompt(
            """You are AurorAI, an Intelligent Document Processing (IDP) engine.
Goal: turn documents into structured, actionable data with compliance-minded care.

Task: classify the document into ONE category from the allowed list AND detect a more specific document_type.

Return ONLY valid JSON with keys:
- category (must match exactly one allowed category)
- document_type (short string, e.g., "invoice", "employment_contract", "medical_intake_form", "loan_application", "court_filing")
- confidence (0.0–1.0)
- rationale (max 2 sentences)

Allowed categories:
Academic Papers; Invoices/Receipts; Contracts; Reports; Personal Documents; Resumes; My Writings & Publications; Uncategorized""",
            f"Classify this document:\n\n{text[:3000] if len(text) > 3000 else text}",
        )

        payload = parse_json_response(response.strip()) or {}
        category = payload.get("category", "Uncategorized")
        document_type = payload.get("document_type", "unknown")
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))
        rationale = str(payload.get("rationale", "Model response could not be validated."))

        if category in CATEGORIES:
            return {
                "category": category,
                "document_type": document_type,
                "confidence": confidence,
                "rationale": rationale,
            }

        for cat in CATEGORIES:
            if cat.lower() in str(response).lower():
                return {
                    "category": cat,
                    "document_type": document_type,
                    "confidence": confidence,
                    "rationale": rationale,
                }

        return {
            "category": infer_category_from_document_type(document_type),
            "document_type": document_type,
            "confidence": confidence,
            "rationale": rationale,
        }
    except Exception as e:
        logging.error(f"AI categorization error: {e}")
        document_type = heuristic_document_type(text)
        return {
            "category": infer_category_from_document_type(document_type),
            "document_type": document_type,
            "confidence": 0.35 if document_type != "unknown" else 0.0,
            "rationale": f"LLM categorization unavailable. Heuristic fallback used: {str(e)}",
        }


async def ai_generate_summary(text: str) -> str:
    """Use AI to generate document summary"""
    try:
        if not get_llm_api_key():
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            preview = lines[:4]
            if not preview:
                return "Summary not available."
            return "Purpose\n- Review uploaded document\n\nKey points\n- " + "\n- ".join(preview[:3])

        response = await run_llm_prompt(
            """You are AurorAI, an IDP assistant. Summarize for operational use.

Output format:
- Purpose (1 sentence)
- Key points (3–6 bullets)
- Decisions / obligations / deadlines (bullets, if any)
- Risks or missing info (bullets, if any)

Keep it under 180 words. No fluff.""",
            f"Summarize this document:\n\n{text[:5000] if len(text) > 5000 else text}",
        )
        return response.strip()
    except Exception as e:
        logging.error(f"AI summary error: {e}")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        preview = lines[:4]
        if not preview:
            return "Summary generation failed."
        return "Purpose\n- Review uploaded document\n\nKey points\n- " + "\n- ".join(preview[:3])


async def ai_extract_fields(text: str) -> Dict[str, Any]:
    """Use AI to extract structured fields with confidence scores."""
    try:
        if not get_llm_api_key():
            extracted = heuristic_extract_fields(text)
            extracted["missing_or_ambiguous"] = extracted.get("missing_or_ambiguous", []) + ["LLM extraction unavailable - heuristic fallback used"]
            return extracted

        response = await run_llm_prompt(
            """You are AurorAI, an Intelligent Document Processing (IDP) extractor.

Extract key fields as structured data. Return ONLY valid JSON:
{
  "document_type": "...",
  "fields": {
     "<field_name>": {"value": "...", "confidence": 0.0-1.0, "evidence": "short quote"}
  },
  "missing_or_ambiguous": ["..."],
  "recommended_next_actions": ["..."]
}

Rules:
- Prefer accuracy over completeness.
- If a field is not present, do NOT guess; add it to missing_or_ambiguous.
- Evidence must be a short snippet from the text (max ~12 words).""",
            f"Extract fields from:\n\n{text[:6000] if len(text) > 6000 else text}",
        )
        parsed = parse_json_response(response.strip())
        if not parsed or not parsed.get("fields"):
            extracted = heuristic_extract_fields(text)
            extracted["missing_or_ambiguous"] = extracted.get("missing_or_ambiguous", []) + ["LLM response was empty or invalid - heuristic fallback used"]
            return extracted
        return parsed
    except Exception as e:
        logging.error(f"AI extraction error: {e}")
        extracted = heuristic_extract_fields(text)
        extracted["missing_or_ambiguous"] = extracted.get("missing_or_ambiguous", []) + [f"Extraction failed - heuristic fallback used: {str(e)}"]
        return extracted


async def ai_extract_citations(text: str) -> List[str]:
    """Use AI to extract and format citations"""
    try:
        if not get_llm_api_key():
            return extract_citations_from_text(text)

        response = await run_llm_prompt(
            """You are a citation extraction expert. Extract all references and citations from the document text.
Format each citation on a new line. Include author names, year, and title when available.
Return ONLY the citations, one per line, no numbering or bullets.""",
            f"Extract all citations from this document:\n\n{text[:6000] if len(text) > 6000 else text}",
        )
        citations = [c.strip() for c in response.strip().split('\n') if c.strip()]
        return citations[:50]
    except Exception as e:
        logging.error(f"AI citation extraction error: {e}")
        return extract_citations_from_text(text)

# Routes
@api_router.get("/")
async def root():
    return {
        "message": "AurorAI IDP API",
        "mission": IDP_MISSION,
        "pipeline": IDP_PIPELINE,
    }


@api_router.get("/idp/pipeline")
async def get_idp_pipeline():
    return {
        "mission": IDP_MISSION,
        "pipeline": IDP_PIPELINE,
        "golden_rules": [
            "Always return one human-readable output and one machine-readable output.",
            "Always include confidence, ambiguity flags, and recommended next actions.",
            "Focus on risk-aware processing for regulated sectors.",
        ],
    }

@api_router.get("/categories")
async def get_categories():
    return {"categories": CATEGORIES}

@api_router.get("/stats")
async def get_stats():
    """Get category statistics"""
    pipeline = [
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    
    results = await db.documents.aggregate(pipeline).to_list(100)
    total = sum(r["count"] for r in results)
    
    stats = []
    for r in results:
        stats.append({
            "category": r["_id"],
            "count": r["count"],
            "percentage": round((r["count"] / total * 100) if total > 0 else 0, 1)
        })
    
    # Add categories with 0 documents
    existing_cats = {s["category"] for s in stats}
    for cat in CATEGORIES:
        if cat not in existing_cats:
            stats.append({"category": cat, "count": 0, "percentage": 0})
    
    return {
        "stats": stats,
        "total_documents": total
    }

@api_router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    _auth: bool = Depends(require_api_token),
):
    """Upload a document (PDF or TXT)."""
    allowed_extensions = {'.pdf', '.txt'}
    file_ext = Path(file.filename).suffix.lower()

    if file_ext in {'.doc', '.docx'}:
        raise HTTPException(
            status_code=400,
            detail="DOC and DOCX upload is not enabled yet because text extraction is not implemented. Use PDF or TXT for now.",
        )

    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type {file_ext} not supported. Allowed: {allowed_extensions}")
    
    # Generate unique filename
    doc_id = str(uuid.uuid4())
    new_filename = f"{doc_id}{file_ext}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / new_filename
    
    # Save file
    content = await file.read()
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    # Extract text and page count (for PDFs)
    text_preview = ""
    page_count = 0
    
    if file_ext == '.pdf':
        text_preview, page_count = extract_pdf_text(str(file_path))
    elif file_ext == '.txt':
        text_preview = content.decode('utf-8', errors='ignore')[:5000]
        page_count = 1
    
    # Create document record
    doc = Document(
        id=doc_id,
        filename=new_filename,
        original_filename=file.filename,
        file_size=len(content),
        page_count=page_count,
        text_preview=text_preview[:2000] if text_preview else "",
        source_hash=compute_sha256_bytes(content),
    )
    
    # Save to database
    doc_dict = doc.model_dump()
    doc_dict["audit_log"] = append_audit_event(
        doc_dict,
        "upload",
        {
            "original_filename": file.filename,
            "file_size": len(content),
            "page_count": page_count,
            "source_hash": doc_dict["source_hash"],
        },
    )
    doc_dict['uploaded_at'] = doc_dict['uploaded_at'].isoformat()
    await db.documents.insert_one(doc_dict)
    
    return doc_dict

@api_router.post("/documents/{doc_id}/categorize")
async def categorize_document(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Use AI to categorize a document"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get full text if needed
    text = doc.get('text_preview', '')
    if not text:
        file_path = UPLOAD_DIR / doc['filename']
        if file_path.exists() and doc['filename'].endswith('.pdf'):
            text, _ = extract_pdf_text(str(file_path))
    
    if not text:
        raise HTTPException(status_code=400, detail="No text content to analyze")
    
    # AI categorization
    classification = await ai_categorize_document(text)
    suggested_category = classification["category"]
    review_required = classification.get("confidence", 0.0) < 0.75
    processing_runs, run_doc = append_processing_run(
        doc,
        "classification",
        triggered_by="categorize_endpoint",
        status="completed",
        details={
            "category": suggested_category,
            "document_type": classification.get("document_type"),
            "confidence": classification.get("confidence"),
        },
    )
    review_decisions = list(doc.get("review_decisions", []))
    current_review_state = "classification_complete"
    if review_required:
        review_decisions, _ = append_review_decision(
            doc,
            run_id=run_doc["id"],
            decision_type="escalate_to_hitl",
            rationale="Classification confidence fell below the configured auto-accept threshold.",
            resulting_state="awaiting_hitl",
        )
        current_review_state = "awaiting_hitl"
    
    # Check if it's academic
    is_academic = suggested_category == "Academic Papers" or "My Writings" in suggested_category
    
    # Update document
    await db.documents.update_one(
        {"id": doc_id},
        {"$set": {
            "ai_suggested_category": suggested_category,
            "category": suggested_category,
            "document_type": classification.get("document_type"),
            "classification_confidence": classification.get("confidence"),
            "classification_rationale": classification.get("rationale"),
            "is_academic": is_academic,
            "review_required": review_required,
            "current_state": "classified",
            "current_review_state": current_review_state,
            "processing_runs": processing_runs,
            "review_decisions": review_decisions,
            "latest_run_id": run_doc["id"],
            "audit_log": append_audit_event(
                doc,
                "categorize",
                {
                    "category": suggested_category,
                    "document_type": classification.get("document_type"),
                    "confidence": classification.get("confidence"),
                },
            ),
        }}
    )
    
    return {
        "document_id": doc_id,
        "suggested_category": suggested_category,
        "document_type": classification.get("document_type"),
        "confidence": classification.get("confidence"),
        "rationale": classification.get("rationale"),
        "is_academic": is_academic
    }

class BulkUploadRequest(BaseModel):
    document_ids: List[str]

@api_router.post("/documents/bulk-categorize")
async def bulk_categorize_documents(
    request: BulkUploadRequest,
    _auth: bool = Depends(require_api_token),
):
    """Bulk categorize multiple documents with AI"""
    results = []
    
    for doc_id in request.document_ids:
        try:
            doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
            if not doc:
                results.append({"document_id": doc_id, "error": "Not found"})
                continue
            
            text = doc.get('text_preview', '')
            if not text:
                file_path = UPLOAD_DIR / doc['filename']
                if file_path.exists() and doc['filename'].endswith('.pdf'):
                    text, _ = extract_pdf_text(str(file_path))
            
            if not text:
                results.append({"document_id": doc_id, "error": "No text content"})
                continue
            
            classification = await ai_categorize_document(text)
            suggested_category = classification["category"]
            is_academic = suggested_category == "Academic Papers" or "My Writings" in suggested_category
            review_required = classification.get("confidence", 0.0) < 0.75
            processing_runs, run_doc = append_processing_run(
                doc,
                "classification",
                triggered_by="bulk_categorize_endpoint",
                status="completed",
                details={
                    "category": suggested_category,
                    "document_type": classification.get("document_type"),
                    "confidence": classification.get("confidence"),
                    "bulk": True,
                },
            )
            review_decisions = list(doc.get("review_decisions", []))
            current_review_state = "classification_complete"
            if review_required:
                review_decisions, _ = append_review_decision(
                    doc,
                    run_id=run_doc["id"],
                    decision_type="escalate_to_hitl",
                    rationale="Bulk classification confidence fell below the configured auto-accept threshold.",
                    resulting_state="awaiting_hitl",
                )
                current_review_state = "awaiting_hitl"
            
            await db.documents.update_one(
                {"id": doc_id},
                {"$set": {
                    "ai_suggested_category": suggested_category,
                    "category": suggested_category,
                    "document_type": classification.get("document_type"),
                    "classification_confidence": classification.get("confidence"),
                    "classification_rationale": classification.get("rationale"),
                    "is_academic": is_academic,
                    "review_required": review_required,
                    "current_state": "classified",
                    "current_review_state": current_review_state,
                    "processing_runs": processing_runs,
                    "review_decisions": review_decisions,
                    "latest_run_id": run_doc["id"],
                    "audit_log": append_audit_event(
                        doc,
                        "bulk_categorize",
                        {
                            "category": suggested_category,
                            "document_type": classification.get("document_type"),
                            "confidence": classification.get("confidence"),
                        },
                    ),
                }}
            )
            
            results.append({
                "document_id": doc_id,
                "suggested_category": suggested_category,
                "document_type": classification.get("document_type"),
                "confidence": classification.get("confidence"),
                "is_academic": is_academic,
                "review_required": review_required,
            })
        except Exception as e:
            results.append({"document_id": doc_id, "error": str(e)})
    
    return {"results": results}

@api_router.post("/documents/{doc_id}/summary")
async def generate_summary(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Generate AI summary for a document"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    text = doc.get('text_preview', '')
    if not text:
        file_path = UPLOAD_DIR / doc['filename']
        if file_path.exists() and doc['filename'].endswith('.pdf'):
            text, _ = extract_pdf_text(str(file_path))
    
    if not text:
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    summary = await ai_generate_summary(text)
    processing_runs, run_doc = append_processing_run(
        doc,
        "summary",
        triggered_by="summary_endpoint",
        status="completed",
        details={"summary_length": len(summary)},
    )
    
    await db.documents.update_one(
        {"id": doc_id},
        {
            "$set": {
                "summary": summary,
                "current_state": "summarized",
                "processing_runs": processing_runs,
                "latest_run_id": run_doc["id"],
                "audit_log": append_audit_event(doc, "summary", {"summary_length": len(summary)}),
            }
        }
    )
    
    return {"document_id": doc_id, "summary": summary}


@api_router.post("/documents/{doc_id}/extract")
async def extract_fields(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Extract machine-readable fields and control signals from a document."""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    text = doc.get('text_preview', '')
    if not text:
        file_path = UPLOAD_DIR / doc['filename']
        if file_path.exists() and doc['filename'].endswith('.pdf'):
            text, _ = extract_pdf_text(str(file_path), max_pages=50)

    if not text:
        raise HTTPException(status_code=400, detail="No text content to analyze")

    extraction = await ai_extract_fields(text)
    fields = extraction.get("fields", {}) if isinstance(extraction.get("fields"), dict) else {}
    controls = run_control_checks(fields)
    review_required = bool(controls["missing_fields"] or controls["invalid_values"])
    processing_runs, run_doc = append_processing_run(
        doc,
        "extraction",
        triggered_by="extract_endpoint",
        status="completed",
        details={
            "field_count": len(fields),
            "missing_count": len(controls["missing_fields"]),
            "invalid_count": len(controls["invalid_values"]),
        },
    )
    review_decisions = list(doc.get("review_decisions", []))
    current_review_state = "extraction_complete"
    if review_required:
        review_decisions, _ = append_review_decision(
            doc,
            run_id=run_doc["id"],
            decision_type="escalate_to_hitl",
            rationale="Control checks identified missing or invalid fields.",
            resulting_state="awaiting_hitl",
        )
        current_review_state = "awaiting_hitl"
    else:
        review_decisions, _ = append_review_decision(
            doc,
            run_id=run_doc["id"],
            decision_type="auto_accept",
            rationale="Extraction passed configured control checks without requiring HITL.",
            resulting_state="auto_approved",
        )
        current_review_state = "auto_approved"

    compliance_note = (
        "Contains potentially sensitive data; apply restricted access and masking where required."
        if any(tag in text.lower() for tag in ["patient", "ssn", "iban", "account", "medical"]) else
        "No obvious sensitive marker found; enforce standard retention and least-privilege access."
    )

    await db.documents.update_one(
        {"id": doc_id},
        {
            "$set": {
                "document_type": extraction.get("document_type") or doc.get("document_type"),
                "extraction": extraction,
                "control_checks": controls,
                "review_required": review_required,
                "compliance_note": compliance_note,
                "current_state": "extracted",
                "current_review_state": current_review_state,
                "processing_runs": processing_runs,
                "review_decisions": review_decisions,
                "latest_run_id": run_doc["id"],
                "audit_log": append_audit_event(
                    doc,
                    "extract",
                    {
                        "field_count": len(fields),
                        "missing_count": len(controls["missing_fields"]),
                        "invalid_count": len(controls["invalid_values"]),
                    },
                ),
            }
        },
    )

    return {
        "document_id": doc_id,
        "document_type": extraction.get("document_type"),
        "data": extraction,
        "controls": controls,
        "compliance_note": compliance_note,
        "recommended_next_actions": extraction.get("recommended_next_actions", []),
        "review_required": review_required,
    }


@api_router.post("/documents/{doc_id}/evidence-package")
async def generate_evidence_package(
    doc_id: str,
    request: EvidencePackageRequest,
    _auth: bool = Depends(require_api_token),
):
    """Build a spec-aligned evidence package for downstream governance ingestion."""
    evidence_record = await create_evidence_record(doc_id, request)
    evidence_record.pop("document_audit_log", None)
    return evidence_record


@api_router.post("/documents/{doc_id}/handoff-to-compassai")
async def handoff_to_compassai(
    doc_id: str,
    request: EvidenceHandoffRequest,
    _auth: bool = Depends(require_api_token),
):
    """Generate an evidence package and hand it off to CompassAI."""
    if not COMPASSAI_INGEST_TOKEN:
        raise HTTPException(status_code=503, detail="COMPASSAI_INGEST_TOKEN is not configured on the server.")

    evidence_record = await create_evidence_record(doc_id, request)
    evidence_record.pop("document_audit_log", None)

    target_base_url = (request.compassai_base_url or COMPASSAI_BASE_URL).rstrip("/")
    response = await post_json(
        f"{target_base_url}/api/v1/evidence",
        {
            "usecase_id": evidence_record["usecase_id"],
            "producer": evidence_record["producer"],
            "artifact_type": evidence_record["artifact_type"],
            "payload": evidence_record["payload"],
            "hash": evidence_record["hash"],
            "control_checks": evidence_record["control_checks"],
        },
        headers={"Authorization": f"Bearer {COMPASSAI_INGEST_TOKEN}"},
    )

    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0}) or {}
    handoff_history, handoff_doc = append_handoff_history(
        doc,
        evidence_id=evidence_record["id"],
        target="compassai",
        target_url=f"{target_base_url}/api/v1/evidence",
        status_code=response["status_code"],
    )

    await db.evidence_handoffs.insert_one(
        {
            "id": handoff_doc["id"],
            "document_id": doc_id,
            "evidence_id": evidence_record["id"],
            "usecase_id": request.usecase_id,
            "target": "compassai",
            "target_url": f"{target_base_url}/api/v1/evidence",
            "status_code": response["status_code"],
            "response": response["body"],
            "created_at": iso_now(),
        }
    )

    await db.documents.update_one(
        {"id": doc_id},
        {
            "$set": {
                "handoff_history": handoff_history,
                "latest_handoff_id": handoff_doc["id"],
                "current_state": "handed_off_to_compassai" if response["status_code"] < 400 else "handoff_failed",
            }
        },
    )

    if response["status_code"] >= 400:
        raise HTTPException(
            status_code=response["status_code"],
            detail={
                "message": "CompassAI rejected the evidence handoff.",
                "compassai_response": response["body"],
                "evidence_id": evidence_record["id"],
            },
        )

    return {
        "message": "Evidence handed off to CompassAI.",
        "evidence": evidence_record,
        "compassai_response": response["body"],
    }

@api_router.post("/documents/{doc_id}/citations")
async def extract_citations(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Extract citations from a document"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    text = doc.get('text_preview', '')
    if not text:
        file_path = UPLOAD_DIR / doc['filename']
        if file_path.exists() and doc['filename'].endswith('.pdf'):
            text, _ = extract_pdf_text(str(file_path), max_pages=50)
    
    if not text:
        raise HTTPException(status_code=400, detail="No text content to analyze")
    
    citations = await ai_extract_citations(text)
    processing_runs, run_doc = append_processing_run(
        doc,
        "citation_extraction",
        triggered_by="citation_endpoint",
        status="completed",
        details={"citation_count": len(citations)},
    )
    
    await db.documents.update_one(
        {"id": doc_id},
        {
            "$set": {
                "citations": citations,
                "current_state": "citations_extracted",
                "processing_runs": processing_runs,
                "latest_run_id": run_doc["id"],
            }
        }
    )
    
    return {"document_id": doc_id, "citations": citations}

@api_router.get("/documents")
async def get_documents(
    category: Optional[str] = None,
    search: Optional[str] = None,
    is_academic: Optional[bool] = None,
    reading_list_id: Optional[str] = None,
    _auth: bool = Depends(require_api_token),
):
    """Get all documents with optional filtering"""
    query = {}
    
    if category:
        query["category"] = category
    if is_academic is not None:
        query["is_academic"] = is_academic
    if reading_list_id:
        query["reading_list_id"] = reading_list_id
    if search:
        query["$or"] = [
            {"original_filename": {"$regex": search, "$options": "i"}},
            {"text_preview": {"$regex": search, "$options": "i"}}
        ]
    
    docs = await db.documents.find(query, {"_id": 0}).sort("uploaded_at", -1).to_list(500)
    
    # Convert datetime strings back
    for doc in docs:
        if isinstance(doc.get('uploaded_at'), str):
            doc['uploaded_at'] = datetime.fromisoformat(doc['uploaded_at'])
    
    return {"documents": docs}

@api_router.get("/documents/{doc_id}")
async def get_document(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Get a single document"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if isinstance(doc.get('uploaded_at'), str):
        doc['uploaded_at'] = datetime.fromisoformat(doc['uploaded_at'])
    
    return doc

@api_router.get("/documents/{doc_id}/download")
async def download_document(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Download a document file"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_path = UPLOAD_DIR / doc['filename']
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on server")
    
    return FileResponse(
        path=str(file_path),
        filename=doc['original_filename'],
        media_type='application/octet-stream'
    )

@api_router.patch("/documents/{doc_id}")
async def update_document(
    doc_id: str,
    update: DocumentUpdate,
    _auth: bool = Depends(require_api_token),
):
    """Update document metadata"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    
    if update_data:
        # Update is_academic based on category
        if 'category' in update_data:
            update_data['is_academic'] = update_data['category'] in ["Academic Papers", "My Writings & Publications"]
        
        await db.documents.update_one({"id": doc_id}, {"$set": update_data})
    
    updated_doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    return updated_doc

@api_router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Delete a document"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete file
    file_path = UPLOAD_DIR / doc['filename']
    if file_path.exists():
        file_path.unlink()
    
    # Delete from database
    await db.documents.delete_one({"id": doc_id})
    
    return {"message": "Document deleted", "id": doc_id}

# Reading Lists
@api_router.post("/reading-lists")
async def create_reading_list(
    data: ReadingListCreate,
    _auth: bool = Depends(require_api_token),
):
    """Create a new reading list"""
    reading_list = ReadingList(
        name=data.name,
        description=data.description
    )
    
    doc = reading_list.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.reading_lists.insert_one(doc)
    
    return reading_list.model_dump()

@api_router.get("/reading-lists")
async def get_reading_lists(
    _auth: bool = Depends(require_api_token),
):
    """Get all reading lists"""
    lists = await db.reading_lists.find({}, {"_id": 0}).to_list(100)
    
    # Get document counts for each list
    for lst in lists:
        count = await db.documents.count_documents({"reading_list_id": lst["id"]})
        lst["document_count"] = count
    
    return {"reading_lists": lists}

@api_router.get("/reading-lists/{list_id}")
async def get_reading_list(
    list_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Get a reading list with its documents"""
    lst = await db.reading_lists.find_one({"id": list_id}, {"_id": 0})
    if not lst:
        raise HTTPException(status_code=404, detail="Reading list not found")
    
    docs = await db.documents.find({"reading_list_id": list_id}, {"_id": 0}).to_list(100)
    lst["documents"] = docs
    
    return lst

@api_router.post("/reading-lists/{list_id}/documents/{doc_id}")
async def add_to_reading_list(
    list_id: str,
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Add a document to a reading list"""
    lst = await db.reading_lists.find_one({"id": list_id})
    if not lst:
        raise HTTPException(status_code=404, detail="Reading list not found")
    
    doc = await db.documents.find_one({"id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    await db.documents.update_one(
        {"id": doc_id},
        {"$set": {"reading_list_id": list_id}}
    )
    
    return {"message": "Document added to reading list"}

@api_router.delete("/reading-lists/{list_id}/documents/{doc_id}")
async def remove_from_reading_list(
    list_id: str,
    doc_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Remove a document from a reading list"""
    await db.documents.update_one(
        {"id": doc_id, "reading_list_id": list_id},
        {"$set": {"reading_list_id": None}}
    )
    
    return {"message": "Document removed from reading list"}

@api_router.delete("/reading-lists/{list_id}")
async def delete_reading_list(
    list_id: str,
    _auth: bool = Depends(require_api_token),
):
    """Delete a reading list"""
    lst = await db.reading_lists.find_one({"id": list_id})
    if not lst:
        raise HTTPException(status_code=404, detail="Reading list not found")
    
    # Remove reading list reference from documents
    await db.documents.update_many(
        {"reading_list_id": list_id},
        {"$set": {"reading_list_id": None}}
    )
    
    await db.reading_lists.delete_one({"id": list_id})
    
    return {"message": "Reading list deleted"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
