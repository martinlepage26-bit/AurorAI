# DocSort - Document Organizer PRD

## Original Problem Statement
Build a document sorter PWA that takes PDFs and documents, sorts them by categories with AI-powered auto-categorization. Features include drag & drop upload, AI categorization with manual override, Paper Builder for academic papers (reading lists, citation extraction, AI summaries).

## Architecture
- **Frontend**: React PWA with Shadcn UI components, Swiss Scholar design theme
- **Backend**: FastAPI with MongoDB
- **AI Integration**: Emergent LLM key with OpenAI GPT-4o-mini for categorization, summaries, citations

## User Personas
1. **Researchers/Academics**: Organize papers, extract citations, build reading lists
2. **Professionals**: Sort invoices, contracts, reports
3. **Personal Users**: Manage personal documents, resumes

## Core Requirements
- Document upload (PDF, DOC, DOCX, TXT)
- AI-powered auto-categorization
- Manual category override
- Document library with search/filter
- Paper Builder with reading lists
- AI summaries and citation extraction

## What's Been Implemented (Jan 2026)
- [x] Dashboard with Quick Stats, Categories breakdown, Recent Documents
- [x] Upload page with drag & drop, file validation, progress tracking
- [x] **Bulk upload with parallel processing (batches of 3)**
- [x] **Bulk upload stats panel (files count, done, processing, failed)**
- [x] **Clear All button for bulk uploads**
- [x] AI categorization on upload (GPT-4o-mini)
- [x] **Bulk AI categorization endpoint**
- [x] Document Library with search, category filter, list/grid views
- [x] Document detail page with metadata, category editing
- [x] Paper Builder with reading list CRUD
- [x] AI summary generation for academic papers
- [x] Citation extraction from documents
- [x] PWA manifest for installability
- [x] Swiss Scholar design theme (Newsreader + Public Sans fonts)

## Categories
1. Academic Papers
2. Invoices/Receipts
3. Contracts
4. Reports
5. Personal Documents
6. Resumes
7. My Writings & Publications
8. Uncategorized

## API Endpoints
- POST /api/documents/upload - Upload document
- POST /api/documents/bulk-categorize - Bulk AI categorization
- POST /api/documents/{id}/categorize - AI categorization
- POST /api/documents/{id}/summary - Generate AI summary
- POST /api/documents/{id}/citations - Extract citations
- GET/PATCH/DELETE /api/documents/{id} - Document CRUD
- GET/POST/DELETE /api/reading-lists - Reading list management

## Prioritized Backlog
### P0 (Critical)
- None - core features complete

### P1 (High Priority)
- Document tags and custom categories
- Export reading list to PDF/BibTeX
- Folder upload support

### P2 (Nice to Have)
- Google Drive/Dropbox integration
- Collaborative reading lists
- Full-text search within documents
- OCR for scanned documents

## Next Tasks
1. Implement document tagging system
2. Add BibTeX export for citations
3. Add folder upload support
