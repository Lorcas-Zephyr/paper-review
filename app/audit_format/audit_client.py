
import asyncio
import base64
import json
import os
import sys
import uuid
import argparse
import httpx
import fitz  # PyMuPDF

# Configuration
SERVER_URL = "http://localhost:8007"
AUDIT_ENDPOINT = f"{SERVER_URL}/audit"

def create_sample_pdf(filename="sample_audit.pdf"):
    """Creates a sample PDF if no file is provided."""
    print(f"Generating sample PDF: {filename}...")
    doc = fitz.open()
    page = doc.new_page()

    # Add some content that triggers audit rules

    # 1. Title
    page.insert_text((50, 40), "1. Introduction", fontsize=18)

    # 2. Semantic Layer Triggers

    # 2.1 TypoChecker (Critical Keywords)
    # rules.yaml: critical_keywords=["TensorFlow", "Pydantic"]
    page.insert_text((50, 70), "We use TensorFlw framework for implementation.", fontsize=11)
    page.insert_text((50, 85), "The Pydantc library is used for validation.", fontsize=11)

    # 2.2 TerminologyChecker (Consistency & Forbidden)
    # rules.yaml: "Deep Learning": ["Deep Learning"], forbidden: ["deep-learning"]
    page.insert_text((50, 110), "Deep Learning is a subset of AI.", fontsize=11)
    page.insert_text((50, 125), "However, deep-learning models are complex.", fontsize=11) # Forbidden
    page.insert_text((50, 140), "DEEP LEARNING requires GPU.", fontsize=11) # Inconsistent case

    # 2.3 PunctuationChecker (Mixed Punctuation & Citation Position)
    # Mixed punctuation
    try:
        # Try inserting Chinese with English punctuation
        page.insert_text((50, 165), "这是一个测试句子.", fontsize=11, fontname="china-s")
    except:
        # Fallback: English with Chinese punctuation (if font allows, otherwise just skip or use unicode)
        # Note: standard fonts might not show Chinese full-width period "。" correctly without CJK font.
        # We will try to use it anyway.
        page.insert_text((50, 165), "This is a test sentence\u3002", fontsize=11, fontname="helv")

    # Citation Position Inconsistency
    page.insert_text((50, 190), "Reference one [1].", fontsize=11) # Correct (usually)
    page.insert_text((50, 205), "Reference two .[2]", fontsize=11) # Incorrect position

    # 2.4 CitationChecker (Style Inconsistency)
    page.insert_text((50, 230), "Another method is proposed by (Wang, 2023).", fontsize=11) # APA style mixed with IEEE [1]

    # 3. Layout Triggers (Existing)
    # Text referencing a missing chart
    try:
        page.insert_text((50, 260), "如图1所示，结果显著。", fontsize=11, fontname="china-s")
        page.insert_text((50, 280), "图1 测试图表", fontsize=10, fontname="china-s")
    except:
        page.insert_text((50, 260), "Figure 1 shows significant results.", fontsize=11)
        page.insert_text((50, 280), "Figure 1 Test Chart", fontsize=10)

    # Formula without reference
    page.insert_text((50, 310), "E = mc^2 (1)", fontsize=11)

    # 4. References Section (Incomplete)
    page.insert_text((50, 350), "References", fontsize=14)
    page.insert_text((50, 370), "[1] A. Smith, 'Paper Title', 2020.", fontsize=10)
    # [2] is missing
    # (Wang, 2023) is missing

    doc.save(filename)
    doc.close()
    return filename

async def send_audit_request(pdf_path: str):
    if not os.path.exists(pdf_path):
        print(f"Error: File not found at {pdf_path}")
        return

    print(f"Reading file: {pdf_path}")
    # Read and encode to Base64
    with open(pdf_path, "rb") as f:
        pdf_content = f.read()
        base64_content = base64.b64encode(pdf_content).decode('utf-8')

    # Construct Payload
    payload = {
        "request_id": f"req_{uuid.uuid4().hex[:8]}",
        "metadata": {
            "paper_id": str(uuid.uuid4()),
            "paper_title": os.path.basename(pdf_path),
            "chunk_id": "chunk_01"
        },
        "payload": {
            "content": base64_content  # Send as Base64
        },
        "config": {
            "temperature": 0.1,
            "max_tokens": 1000
        }
    }

    print(f"Sending request to {AUDIT_ENDPOINT}...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(AUDIT_ENDPOINT, json=payload)

            if response.status_code == 200:
                result = response.json()
                print("\n✅ Audit Success!")
                print("-" * 50)
                print(f"Score: {result['result']['score']}")
                print(f"Level: {result['result']['audit_level']}")
                print(f"Tags:  {result['result']['tags']}")
                print("-" * 50)
                print("Full Response:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(f"\n❌ Server returned error {response.status_code}:")
                print(response.text)

        except httpx.ConnectError:
            print(f"\n❌ Connection failed. Is the server running at {SERVER_URL}?")
            print("Run 'python main.py' in another terminal first.")
        except Exception as e:
            print(f"\n❌ Error occurred: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Send a PDF to the Standardization Auditor Agent.")
    parser.add_argument("file", nargs="?", help="Path to the PDF file to audit. If omitted, a sample PDF is generated.")
    args = parser.parse_args()

    pdf_path = args.file
    is_generated = False

    if not pdf_path:
        pdf_path = create_sample_pdf()
        is_generated = True

    try:
        asyncio.run(send_audit_request(pdf_path))
    finally:
        # Cleanup if we generated the file
        if is_generated and os.path.exists(pdf_path):
            print(f"\nCleaning up generated file: {pdf_path}")
            os.remove(pdf_path)

if __name__ == "__main__":
    main()
