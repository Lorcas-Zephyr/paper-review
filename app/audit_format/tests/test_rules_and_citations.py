import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


class TestLayoutFindCitations(unittest.TestCase):
    def test_find_citations_skips_code_like_numeric(self):
        from core.layout_analysis import _find_citations

        text = "for i in range(3): a[i] = 1; see [1] and [2]"
        matches = _find_citations(text)
        self.assertFalse(any(m in {"[1]", "[2]"} for m in matches))

    def test_find_citations_keeps_author_year(self):
        from core.layout_analysis import _find_citations

        text = "This is based on (Smith, 2020) and (O'Neil et al., 2019a)."
        matches = _find_citations(text)
        self.assertTrue(any("2020" in m for m in matches))
        self.assertTrue(any("2019" in m for m in matches))

    def test_find_citations_filters_large_and_zero(self):
        from core.layout_analysis import _find_citations

        text = "Bad refs [0] [1000] ok [12, 13]"
        matches = _find_citations(text)
        self.assertIn("[12, 13]", matches)
        self.assertNotIn("[0]", matches)
        self.assertNotIn("[1000]", matches)


class TestCitationReferenceMatch(unittest.TestCase):
    def test_missing_reference_detected(self):
        from core.layout_rules import check_citation_reference_match

        refs = [
            SimpleNamespace(content="[1] A", page_num=10, bbox=[0, 0, 1, 1]),
            SimpleNamespace(content="[2] B", page_num=10, bbox=[0, 0, 1, 1]),
        ]
        cites = [
            SimpleNamespace(content="[1]", page_num=1, bbox=[0, 0, 1, 1]),
            SimpleNamespace(content="[3]", page_num=1, bbox=[0, 0, 1, 1]),
        ]
        issues = check_citation_reference_match(cites, refs)
        self.assertEqual(len(issues), 1)
        self.assertIn("3", issues[0].message)

    def test_year_like_citation_ignored(self):
        from core.layout_rules import check_citation_reference_match

        refs = [SimpleNamespace(content="[1] A", page_num=10, bbox=[0, 0, 1, 1])]
        cites = [SimpleNamespace(content="[2019]", page_num=1, bbox=[0, 0, 1, 1])]
        issues = check_citation_reference_match(cites, refs)
        self.assertEqual(issues, [])

    def test_large_number_citation_ignored(self):
        from core.layout_rules import check_citation_reference_match

        refs = [SimpleNamespace(content="[1] A", page_num=10, bbox=[0, 0, 1, 1])]
        cites = [
            SimpleNamespace(content="[500]", page_num=1, bbox=[0, 0, 1, 1]),
            SimpleNamespace(content="[1000]", page_num=1, bbox=[0, 0, 1, 1]),
        ]
        issues = check_citation_reference_match(cites, refs)
        self.assertEqual(issues, [])

    def test_far_beyond_reference_range_filtered(self):
        from core.layout_rules import check_citation_reference_match

        refs = [SimpleNamespace(content=f"[{i}] R", page_num=10, bbox=[0, 0, 1, 1]) for i in range(1, 6)]
        cites = [SimpleNamespace(content="[20]", page_num=1, bbox=[0, 0, 1, 1])]
        issues = check_citation_reference_match(cites, refs)
        self.assertEqual(issues, [])
