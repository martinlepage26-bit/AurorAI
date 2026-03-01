#!/usr/bin/env python3
"""
DocSort Scanner - Scan your laptop for documents and upload to DocSort
Usage: python scan_and_upload.py [directory]

Examples:
  python scan_and_upload.py                    # Scan common folders
  python scan_and_upload.py /path/to/folder    # Scan specific folder
  python scan_and_upload.py ~/Documents        # Scan Documents folder
"""

import os
import sys
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================
# CONFIGURATION - Update this URL if needed
# ============================================
DOCSORT_API = "https://paper-organizer-2.preview.emergentagent.com/api"

# File types to scan
EXTENSIONS = {'.pdf', '.doc', '.docx', '.txt'}

# Max file size (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Folders to skip
SKIP_FOLDERS = {
    'node_modules', '.git', '__pycache__', 'venv', '.venv',
    'Library', 'AppData', 'Application Data', '.Trash',
    'Windows', 'Program Files', 'Program Files (x86)'
}

def get_default_scan_paths():
    """Get common document locations based on OS"""
    home = Path.home()
    paths = [
        home / "Documents",
        home / "Downloads",
        home / "Desktop",
    ]
    # Add OneDrive/Dropbox/Google Drive if they exist
    for cloud in ["OneDrive", "Dropbox", "Google Drive"]:
        cloud_path = home / cloud
        if cloud_path.exists():
            paths.append(cloud_path)
    
    return [p for p in paths if p.exists()]

def find_documents(search_paths):
    """Recursively find all documents in given paths"""
    documents = []
    
    for base_path in search_paths:
        print(f"📂 Scanning: {base_path}")
        
        for root, dirs, files in os.walk(base_path):
            # Skip system/cache folders
            dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS and not d.startswith('.')]
            
            for file in files:
                if file.startswith('.'):
                    continue
                    
                ext = Path(file).suffix.lower()
                if ext in EXTENSIONS:
                    file_path = Path(root) / file
                    try:
                        size = file_path.stat().st_size
                        if size <= MAX_FILE_SIZE and size > 0:
                            documents.append({
                                'path': file_path,
                                'name': file,
                                'size': size
                            })
                    except (PermissionError, OSError):
                        continue
    
    return documents

def upload_document(doc):
    """Upload a single document to DocSort"""
    try:
        with open(doc['path'], 'rb') as f:
            files = {'file': (doc['name'], f)}
            response = requests.post(
                f"{DOCSORT_API}/documents/upload",
                files=files,
                timeout=60
            )
        
        if response.status_code == 200:
            return {'success': True, 'name': doc['name'], 'id': response.json().get('id')}
        else:
            return {'success': False, 'name': doc['name'], 'error': response.text}
    
    except Exception as e:
        return {'success': False, 'name': doc['name'], 'error': str(e)}

def categorize_documents(doc_ids):
    """Bulk categorize uploaded documents"""
    try:
        response = requests.post(
            f"{DOCSORT_API}/documents/bulk-categorize",
            json={"document_ids": doc_ids},
            timeout=300
        )
        return response.status_code == 200
    except:
        return False

def format_size(size):
    """Format file size for display"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f} KB"
    else:
        return f"{size/(1024*1024):.1f} MB"

def main():
    print("=" * 50)
    print("📄 DocSort Scanner")
    print("=" * 50)
    
    # Determine scan paths
    if len(sys.argv) > 1:
        scan_path = Path(sys.argv[1]).expanduser()
        if not scan_path.exists():
            print(f"❌ Path not found: {scan_path}")
            sys.exit(1)
        search_paths = [scan_path]
    else:
        search_paths = get_default_scan_paths()
        print(f"Scanning default locations: {', '.join(str(p) for p in search_paths)}")
    
    print()
    
    # Find documents
    print("🔍 Finding documents...")
    documents = find_documents(search_paths)
    
    if not documents:
        print("❌ No documents found!")
        sys.exit(0)
    
    # Summary
    total_size = sum(d['size'] for d in documents)
    by_type = {}
    for doc in documents:
        ext = Path(doc['name']).suffix.lower()
        by_type[ext] = by_type.get(ext, 0) + 1
    
    print(f"\n📊 Found {len(documents)} documents ({format_size(total_size)})")
    for ext, count in sorted(by_type.items()):
        print(f"   {ext}: {count} files")
    
    # Confirm upload
    print()
    response = input(f"Upload all {len(documents)} documents to DocSort? (y/n): ").lower().strip()
    if response != 'y':
        print("Cancelled.")
        sys.exit(0)
    
    # Upload
    print(f"\n⬆️  Uploading documents...")
    uploaded_ids = []
    failed = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(upload_document, doc): doc for doc in documents}
        
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            progress = f"[{i}/{len(documents)}]"
            
            if result['success']:
                uploaded_ids.append(result['id'])
                print(f"  ✅ {progress} {result['name']}")
            else:
                failed.append(result)
                print(f"  ❌ {progress} {result['name']} - {result['error'][:50]}")
    
    # Categorize
    if uploaded_ids:
        print(f"\n🤖 Running AI categorization on {len(uploaded_ids)} documents...")
        if categorize_documents(uploaded_ids):
            print("  ✅ AI categorization complete!")
        else:
            print("  ⚠️  Some documents may need manual categorization")
    
    # Summary
    print("\n" + "=" * 50)
    print("📋 SUMMARY")
    print("=" * 50)
    print(f"  ✅ Uploaded: {len(uploaded_ids)}")
    print(f"  ❌ Failed: {len(failed)}")
    print(f"\n🌐 View your documents at:")
    print(f"   {DOCSORT_API.replace('/api', '')}/library")
    print()

if __name__ == "__main__":
    main()
