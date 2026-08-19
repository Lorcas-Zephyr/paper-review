import base64
import sys
import unittest
from io import BytesIO
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


class TestModels(unittest.TestCase):
    def test_issue_detail_normalization(self):
        from models import IssueDetail

        issue = IssueDetail(
            issue_type="X",
            severity="Info",
            page_num="-1",
            bbox=[1, 2],
        )
        self.assertEqual(issue.page_num, 0)
        self.assertEqual(issue.bbox, [1.0, 2.0, 0.0, 0.0])

    def test_audit_request_minimal(self):
        from models import AuditRequest

        req = AuditRequest.model_validate(
            {
                "request_id": "req_1",
                "metadata": {
                    "paper_id": "123e4567-e89b-42d3-a456-426614174000",
                    "paper_title": "t",
                    "chunk_id": "c1",
                },
                "payload": {"content": "hello"},
                "config": {"temperature": 0.1, "max_tokens": 1},
            }
        )
        self.assertEqual(req.request_id, "req_1")
        self.assertEqual(req.payload.content, "hello")


class TestPdfUtils(unittest.TestCase):
    def _make_pdf_bytes(self) -> bytes:
        from reportlab.pdfgen import canvas

        buf = BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 720, "Hello [1] World (Smith, 2020)")
        c.showPage()
        c.save()
        return buf.getvalue()

    def test_open_pdf_accepts_bytes_and_base64(self):
        from core.pdf_utils import open_pdf

        pdf_bytes = self._make_pdf_bytes()
        doc1 = open_pdf(pdf_bytes)
        try:
            self.assertGreaterEqual(len(doc1), 1)
        finally:
            doc1.close()

        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        doc2 = open_pdf(b64)
        try:
            self.assertGreaterEqual(len(doc2), 1)
        finally:
            doc2.close()

    def test_open_pdf_rejects_invalid(self):
        from core.pdf_utils import open_pdf

        with self.assertRaises(ValueError):
            open_pdf("not a pdf")
