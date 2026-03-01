from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import fitz  # PyMuPDF
import aiofiles
import tempfile
import json
import re

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

# Define Models
class Document(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    original_filename: str
    category: str = "Uncategorized"
    ai_suggested_category: Optional[str] = None
    file_size: int = 0
    page_count: int = 0
    text_preview: str = ""
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_academic: bool = False
    citations: List[str] = []
    summary: Optional[str] = None
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
async def ai_categorize_document(text: str) -> str:
    """Use AI to categorize document based on content"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            return "Uncategorized"
        
        chat = LlmChat(
            api_key=api_key,
            session_id=str(uuid.uuid4()),
            system_message="""You are a document categorization expert. Analyze the document text and categorize it into ONE of these categories:
- Academic Papers (research papers, journal articles, scientific studies)
- Invoices/Receipts (bills, payment records, financial documents)
- Contracts (legal agreements, terms of service, contracts)
- Reports (business reports, annual reports, analysis documents)
- Personal Documents (IDs, certificates, personal letters)
- Resumes (CVs, job applications, professional profiles)
- My Writings & Publications (personal writings, drafts, publications)
- Uncategorized (if none of the above fit)

Respond with ONLY the category name, nothing else."""
        ).with_model("openai", "gpt-4o-mini")
        
        # Truncate text for API
        truncated_text = text[:3000] if len(text) > 3000 else text
        
        user_message = UserMessage(text=f"Categorize this document:\n\n{truncated_text}")
        response = await chat.send_message(user_message)
        
        # Validate response is a valid category
        response_clean = response.strip()
        if response_clean in CATEGORIES:
            return response_clean
        
        # Try to match partial
        for cat in CATEGORIES:
            if cat.lower() in response_clean.lower():
                return cat
        
        return "Uncategorized"
    except Exception as e:
        logging.error(f"AI categorization error: {e}")
        return "Uncategorized"

async def ai_generate_summary(text: str) -> str:
    """Use AI to generate document summary"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            return "Summary not available - API key missing"
        
        chat = LlmChat(
            api_key=api_key,
            session_id=str(uuid.uuid4()),
            system_message="""You are an expert at summarizing documents. Provide a clear, concise summary of the document covering:
1. Main topic/purpose
2. Key points or findings
3. Conclusions or recommendations (if applicable)

Keep the summary under 200 words."""
        ).with_model("openai", "gpt-4o-mini")
        
        truncated_text = text[:5000] if len(text) > 5000 else text
        
        user_message = UserMessage(text=f"Summarize this document:\n\n{truncated_text}")
        response = await chat.send_message(user_message)
        
        return response.strip()
    except Exception as e:
        logging.error(f"AI summary error: {e}")
        return f"Summary generation failed: {str(e)}"

async def ai_extract_citations(text: str) -> List[str]:
    """Use AI to extract and format citations"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            return extract_citations_from_text(text)
        
        chat = LlmChat(
            api_key=api_key,
            session_id=str(uuid.uuid4()),
            system_message="""You are a citation extraction expert. Extract all references and citations from the document text.
Format each citation on a new line. Include author names, year, and title when available.
Return ONLY the citations, one per line, no numbering or bullets."""
        ).with_model("openai", "gpt-4o-mini")
        
        truncated_text = text[:6000] if len(text) > 6000 else text
        
        user_message = UserMessage(text=f"Extract all citations from this document:\n\n{truncated_text}")
        response = await chat.send_message(user_message)
        
        # Parse response into list
        citations = [c.strip() for c in response.strip().split('\n') if c.strip()]
        return citations[:50]  # Max 50 citations
    except Exception as e:
        logging.error(f"AI citation extraction error: {e}")
        return extract_citations_from_text(text)

# Routes
@api_router.get("/")
async def root():
    return {"message": "Document Sorter API"}

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
async def upload_document(file: UploadFile = File(...)):
    """Upload a document (PDF, DOC, DOCX, TXT)"""
    allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type {file_ext} not supported. Allowed: {allowed_extensions}")
    
    # Generate unique filename
    doc_id = str(uuid.uuid4())
    new_filename = f"{doc_id}{file_ext}"
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
        text_preview=text_preview[:2000] if text_preview else ""
    )
    
    # Save to database
    doc_dict = doc.model_dump()
    doc_dict['uploaded_at'] = doc_dict['uploaded_at'].isoformat()
    await db.documents.insert_one(doc_dict)
    
    return doc.model_dump()

@api_router.post("/documents/{doc_id}/categorize")
async def categorize_document(doc_id: str):
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
    suggested_category = await ai_categorize_document(text)
    
    # Check if it's academic
    is_academic = suggested_category == "Academic Papers" or "My Writings" in suggested_category
    
    # Update document
    await db.documents.update_one(
        {"id": doc_id},
        {"$set": {
            "ai_suggested_category": suggested_category,
            "category": suggested_category,
            "is_academic": is_academic
        }}
    )
    
    return {
        "document_id": doc_id,
        "suggested_category": suggested_category,
        "is_academic": is_academic
    }

class BulkUploadRequest(BaseModel):
    document_ids: List[str]

@api_router.post("/documents/bulk-categorize")
async def bulk_categorize_documents(request: BulkUploadRequest):
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
            
            suggested_category = await ai_categorize_document(text)
            is_academic = suggested_category == "Academic Papers" or "My Writings" in suggested_category
            
            await db.documents.update_one(
                {"id": doc_id},
                {"$set": {
                    "ai_suggested_category": suggested_category,
                    "category": suggested_category,
                    "is_academic": is_academic
                }}
            )
            
            results.append({
                "document_id": doc_id,
                "suggested_category": suggested_category,
                "is_academic": is_academic
            })
        except Exception as e:
            results.append({"document_id": doc_id, "error": str(e)})
    
    return {"results": results}

@api_router.post("/documents/{doc_id}/summary")
async def generate_summary(doc_id: str):
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
    
    await db.documents.update_one(
        {"id": doc_id},
        {"$set": {"summary": summary}}
    )
    
    return {"document_id": doc_id, "summary": summary}

@api_router.post("/documents/{doc_id}/citations")
async def extract_citations(doc_id: str):
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
    
    await db.documents.update_one(
        {"id": doc_id},
        {"$set": {"citations": citations}}
    )
    
    return {"document_id": doc_id, "citations": citations}

@api_router.get("/documents")
async def get_documents(
    category: Optional[str] = None,
    search: Optional[str] = None,
    is_academic: Optional[bool] = None,
    reading_list_id: Optional[str] = None
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
async def get_document(doc_id: str):
    """Get a single document"""
    doc = await db.documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if isinstance(doc.get('uploaded_at'), str):
        doc['uploaded_at'] = datetime.fromisoformat(doc['uploaded_at'])
    
    return doc

@api_router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str):
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
async def update_document(doc_id: str, update: DocumentUpdate):
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
async def delete_document(doc_id: str):
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
async def create_reading_list(data: ReadingListCreate):
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
async def get_reading_lists():
    """Get all reading lists"""
    lists = await db.reading_lists.find({}, {"_id": 0}).to_list(100)
    
    # Get document counts for each list
    for lst in lists:
        count = await db.documents.count_documents({"reading_list_id": lst["id"]})
        lst["document_count"] = count
    
    return {"reading_lists": lists}

@api_router.get("/reading-lists/{list_id}")
async def get_reading_list(list_id: str):
    """Get a reading list with its documents"""
    lst = await db.reading_lists.find_one({"id": list_id}, {"_id": 0})
    if not lst:
        raise HTTPException(status_code=404, detail="Reading list not found")
    
    docs = await db.documents.find({"reading_list_id": list_id}, {"_id": 0}).to_list(100)
    lst["documents"] = docs
    
    return lst

@api_router.post("/reading-lists/{list_id}/documents/{doc_id}")
async def add_to_reading_list(list_id: str, doc_id: str):
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
async def remove_from_reading_list(list_id: str, doc_id: str):
    """Remove a document from a reading list"""
    await db.documents.update_one(
        {"id": doc_id, "reading_list_id": list_id},
        {"$set": {"reading_list_id": None}}
    )
    
    return {"message": "Document removed from reading list"}

@api_router.delete("/reading-lists/{list_id}")
async def delete_reading_list(list_id: str):
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
