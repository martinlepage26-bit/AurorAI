import requests
import sys
import json
import tempfile
import os
from datetime import datetime
from pathlib import Path

class DocumentSorterAPITester:
    def __init__(self, base_url="https://paper-organizer-2.preview.emergentagent.com/api"):
        self.base_url = base_url
        self.tests_run = 0
        self.tests_passed = 0
        self.document_ids = []
        self.reading_list_ids = []

    def run_test(self, name, method, endpoint, expected_status, data=None, files=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        headers = {'Content-Type': 'application/json'} if not files else {}

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers)
            elif method == 'POST':
                if files:
                    response = requests.post(url, files=files)
                else:
                    response = requests.post(url, json=data, headers=headers)
            elif method == 'PATCH':
                response = requests.patch(url, json=data, headers=headers)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers)

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                try:
                    return success, response.json() if response.text else {}
                except:
                    return success, {}
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                print(f"   Response: {response.text[:200]}")

            return success, {}

        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            return False, {}

    def test_health_check(self):
        """Test API health check"""
        success, response = self.run_test(
            "API Health Check",
            "GET",
            "",
            200
        )
        return success

    def test_get_categories(self):
        """Test getting categories"""
        success, response = self.run_test(
            "Get Categories",
            "GET", 
            "categories",
            200
        )
        if success:
            expected_categories = ["Academic Papers", "Invoices/Receipts", "Contracts", "Reports", "Personal Documents", "Resumes", "My Writings & Publications", "Uncategorized"]
            actual_categories = response.get('categories', [])
            if set(expected_categories).issubset(set(actual_categories)):
                print(f"   Categories match expected: {len(actual_categories)} categories found")
            else:
                print(f"   ⚠️  Categories mismatch. Expected {expected_categories}, got {actual_categories}")
        return success

    def test_get_stats(self):
        """Test getting dashboard stats"""
        success, response = self.run_test(
            "Get Dashboard Stats",
            "GET",
            "stats", 
            200
        )
        if success:
            total_docs = response.get('total_documents', 0)
            stats = response.get('stats', [])
            print(f"   Total documents: {total_docs}, Stats categories: {len(stats)}")
        return success

    def create_test_pdf(self):
        """Create a test PDF file for upload testing"""
        try:
            # Create a simple test PDF using reportlab if available, otherwise use text file
            test_content = """Test Academic Paper
            
This is a test document for the Document Sorter application.

Abstract:
This paper explores the effectiveness of automated document categorization systems.

1. Introduction
Document management is a critical aspect of modern workflows.

2. Methodology 
We implemented an AI-powered categorization system.

3. Results
The system achieved 95% accuracy in document classification.

References:
[1] Smith, J. (2023). Document Classification in the Digital Age.
[2] Brown, A. et al. (2022). Machine Learning for Text Analysis.
"""
            # Create temporary PDF-like file (will be treated as text for testing)
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            temp_file.write(test_content)
            temp_file.close()
            return temp_file.name
        except Exception as e:
            print(f"Error creating test file: {e}")
            return None

    def test_upload_document(self):
        """Test document upload"""
        test_file_path = self.create_test_pdf()
        if not test_file_path:
            return False

        try:
            with open(test_file_path, 'rb') as f:
                files = {'file': ('test_academic_paper.txt', f, 'text/plain')}
                success, response = self.run_test(
                    "Upload Document",
                    "POST",
                    "documents/upload",
                    200,
                    files=files
                )
            
            if success:
                doc_id = response.get('id')
                if doc_id:
                    self.document_ids.append(doc_id)
                    print(f"   Document uploaded with ID: {doc_id}")
                    print(f"   File size: {response.get('file_size', 0)} bytes")
                    return True
            return False
        except Exception as e:
            print(f"Error in upload test: {e}")
            return False
        finally:
            if os.path.exists(test_file_path):
                os.unlink(test_file_path)

    def test_ai_categorization(self):
        """Test AI document categorization"""
        if not self.document_ids:
            print("❌ No documents to categorize")
            return False
        
        doc_id = self.document_ids[0]
        success, response = self.run_test(
            "AI Document Categorization",
            "POST",
            f"documents/{doc_id}/categorize",
            200
        )
        
        if success:
            suggested_category = response.get('suggested_category')
            is_academic = response.get('is_academic')
            print(f"   AI suggested category: {suggested_category}")
            print(f"   Is academic: {is_academic}")
        return success

    def test_get_documents(self):
        """Test getting documents list"""
        success, response = self.run_test(
            "Get Documents List",
            "GET",
            "documents",
            200
        )
        
        if success:
            documents = response.get('documents', [])
            print(f"   Found {len(documents)} documents")
        return success

    def test_get_document_detail(self):
        """Test getting single document details"""
        if not self.document_ids:
            print("❌ No documents to get details for")
            return False
        
        doc_id = self.document_ids[0]
        success, response = self.run_test(
            "Get Document Details",
            "GET",
            f"documents/{doc_id}",
            200
        )
        
        if success:
            filename = response.get('original_filename', 'Unknown')
            category = response.get('category', 'Unknown')
            print(f"   Document: {filename}, Category: {category}")
        return success

    def test_ai_summary_generation(self):
        """Test AI summary generation"""
        if not self.document_ids:
            print("❌ No documents for summary generation")
            return False
        
        doc_id = self.document_ids[0]
        success, response = self.run_test(
            "AI Summary Generation",
            "POST",
            f"documents/{doc_id}/summary",
            200
        )
        
        if success:
            summary = response.get('summary', '')
            print(f"   Summary generated: {len(summary)} characters")
            if "not available" in summary.lower() or "failed" in summary.lower():
                print(f"   ⚠️  Summary indicates API issue: {summary[:100]}")
        return success

    def test_citation_extraction(self):
        """Test citation extraction"""
        if not self.document_ids:
            print("❌ No documents for citation extraction")
            return False
        
        doc_id = self.document_ids[0]
        success, response = self.run_test(
            "Citation Extraction", 
            "POST",
            f"documents/{doc_id}/citations",
            200
        )
        
        if success:
            citations = response.get('citations', [])
            print(f"   Found {len(citations)} citations")
        return success

    def test_create_reading_list(self):
        """Test creating reading lists"""
        success, response = self.run_test(
            "Create Reading List",
            "POST",
            "reading-lists",
            200,
            data={"name": "Test Reading List", "description": "Test list for automated testing"}
        )
        
        if success:
            list_id = response.get('id')
            if list_id:
                self.reading_list_ids.append(list_id)
                print(f"   Reading list created with ID: {list_id}")
        return success

    def test_get_reading_lists(self):
        """Test getting reading lists"""
        success, response = self.run_test(
            "Get Reading Lists",
            "GET", 
            "reading-lists",
            200
        )
        
        if success:
            lists = response.get('reading_lists', [])
            print(f"   Found {len(lists)} reading lists")
        return success

    def test_add_document_to_reading_list(self):
        """Test adding document to reading list"""
        if not self.document_ids or not self.reading_list_ids:
            print("❌ Missing documents or reading lists")
            return False
        
        doc_id = self.document_ids[0]
        list_id = self.reading_list_ids[0]
        
        success, response = self.run_test(
            "Add Document to Reading List",
            "POST",
            f"reading-lists/{list_id}/documents/{doc_id}",
            200
        )
        return success

    def test_update_document_category(self):
        """Test updating document category"""
        if not self.document_ids:
            print("❌ No documents to update")
            return False
        
        doc_id = self.document_ids[0]
        success, response = self.run_test(
            "Update Document Category",
            "PATCH",
            f"documents/{doc_id}",
            200,
            data={"category": "Academic Papers"}
        )
        return success

    def test_filter_documents_by_category(self):
        """Test filtering documents by category"""
        success, response = self.run_test(
            "Filter Documents by Category",
            "GET",
            "documents?category=Academic Papers",
            200
        )
        
        if success:
            documents = response.get('documents', [])
            print(f"   Found {len(documents)} academic papers")
        return success

    def test_delete_document(self):
        """Test deleting a document (cleanup)"""
        if not self.document_ids:
            return True
        
        doc_id = self.document_ids[0]
        success, response = self.run_test(
            "Delete Document",
            "DELETE",
            f"documents/{doc_id}",
            200
        )
        return success

    def test_delete_reading_list(self):
        """Test deleting reading list (cleanup)"""
        if not self.reading_list_ids:
            return True
        
        list_id = self.reading_list_ids[0]
        success, response = self.run_test(
            "Delete Reading List",
            "DELETE", 
            f"reading-lists/{list_id}",
            200
        )
        return success

def main():
    print("🚀 Starting Document Sorter API Tests")
    print(f"Testing against: https://paper-organizer-2.preview.emergentagent.com/api")
    
    tester = DocumentSorterAPITester()

    # Core API Tests
    tests = [
        tester.test_health_check,
        tester.test_get_categories,
        tester.test_get_stats,
        tester.test_upload_document,
        tester.test_ai_categorization,
        tester.test_get_documents,
        tester.test_get_document_detail,
        tester.test_update_document_category,
        tester.test_filter_documents_by_category,
        tester.test_ai_summary_generation,
        tester.test_citation_extraction,
        tester.test_create_reading_list,
        tester.test_get_reading_lists,
        tester.test_add_document_to_reading_list,
        # Cleanup
        tester.test_delete_document,
        tester.test_delete_reading_list,
    ]

    print(f"\n📋 Running {len(tests)} API tests...")
    
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            tester.tests_run += 1

    # Print results
    print(f"\n📊 Test Results:")
    print(f"Tests passed: {tester.tests_passed}/{tester.tests_run}")
    print(f"Success rate: {(tester.tests_passed/tester.tests_run*100):.1f}%")
    
    if tester.tests_passed < tester.tests_run * 0.7:  # Less than 70% success
        print("\n❌ Critical backend issues detected!")
        return 1
    elif tester.tests_passed < tester.tests_run:
        print("\n⚠️  Some backend issues detected")
        return 0
    else:
        print("\n✅ All backend tests passed!")
        return 0

if __name__ == "__main__":
    sys.exit(main())