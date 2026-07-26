"""
tests/test_scorer.py — Unit tests for scorer formatting and JSON parsing utilities.

Tests cover:
  - Score bar generation
  - Score emoji selection
  - ATS result message formatting
  - Comparison summary formatting
  - LLM response JSON parsing (via llm_client._parse_response)
"""

import sys
import os
import json
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.scorer import (
    _score_bar,
    _score_emoji,
    format_ats_result,
    format_comparison_summary,
)
from core.llm_client import _parse_response, _clean_json_string


# ---------------------------------------------------------------------------
# Sample ATSResult for reuse across tests
# ---------------------------------------------------------------------------

SAMPLE_RESULT = {
    "score": 75,
    "strengths": ["Strong Python skills", "Relevant cloud experience"],
    "missing_keywords": ["Kubernetes", "Terraform", "CI/CD pipelines"],
    "suggestions": [
        "Add a dedicated 'Skills' section listing Kubernetes and Terraform.",
        "Quantify achievements with metrics (e.g. 'reduced deployment time by 40%').",
    ],
    "course_suggestions": [
        "Kubernetes for Beginners – Udemy",
        "HashiCorp Terraform Associate – Coursera",
    ],
    "follow_up_questions": [
        "Do you have any experience with Kubernetes, even in a learning context?",
        "Have you used any CI/CD tools like Jenkins, GitHub Actions, or GitLab CI?",
        "Can you describe your AWS experience in more detail?",
    ],
}


class TestScoreBar(unittest.TestCase):
    def test_zero_score(self):
        bar = _score_bar(0)
        self.assertEqual(bar, "░" * 20)

    def test_full_score(self):
        bar = _score_bar(100)
        self.assertEqual(bar, "█" * 20)

    def test_midpoint_score(self):
        bar = _score_bar(50)
        self.assertEqual(len(bar), 20)
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_bar_length_always_20(self):
        for score in range(0, 101, 5):
            self.assertEqual(len(_score_bar(score)), 20)


class TestScoreEmoji(unittest.TestCase):
    def test_green_above_80(self):
        self.assertEqual(_score_emoji(80), "🟢")
        self.assertEqual(_score_emoji(95), "🟢")

    def test_yellow_60_to_79(self):
        self.assertEqual(_score_emoji(60), "🟡")
        self.assertEqual(_score_emoji(79), "🟡")

    def test_orange_40_to_59(self):
        self.assertEqual(_score_emoji(40), "🟠")
        self.assertEqual(_score_emoji(59), "🟠")

    def test_red_below_40(self):
        self.assertEqual(_score_emoji(0), "🔴")
        self.assertEqual(_score_emoji(39), "🔴")


class TestFormatATSResult(unittest.TestCase):
    def setUp(self):
        self.result = SAMPLE_RESULT.copy()

    def test_output_contains_score(self):
        msg = format_ats_result(self.result, "Test Resume")
        self.assertIn("75/100", msg)

    def test_output_contains_label(self):
        msg = format_ats_result(self.result, "My Resume v2")
        self.assertIn("My Resume v2", msg)

    def test_output_contains_strengths(self):
        msg = format_ats_result(self.result)
        self.assertIn("Python", msg)

    def test_output_contains_missing_keywords(self):
        msg = format_ats_result(self.result)
        self.assertIn("Kubernetes", msg)

    def test_output_contains_suggestions(self):
        msg = format_ats_result(self.result)
        self.assertIn("Skills", msg)

    def test_output_contains_courses(self):
        msg = format_ats_result(self.result)
        self.assertIn("Udemy", msg)

    def test_output_contains_followup_questions(self):
        msg = format_ats_result(self.result)
        self.assertIn("Q1", msg)
        self.assertIn("Q2", msg)

    def test_empty_result_does_not_crash(self):
        msg = format_ats_result({"score": 0}, "Empty Resume")
        self.assertIn("0/100", msg)

    def test_score_clamped_to_100(self):
        msg = format_ats_result({"score": 150}, "Test")
        # Score should be clamped to 100 during LLM parsing — but formatter
        # should at least not crash on unusual values
        self.assertIn("/100", msg)


class TestFormatComparisonSummary(unittest.TestCase):
    def _make_entry(self, label, score):
        result = SAMPLE_RESULT.copy()
        result["score"] = score
        return {"label": label, "result": result}

    def test_sorted_by_score_descending(self):
        entries = [
            self._make_entry("Resume A", 60),
            self._make_entry("Resume B", 85),
            self._make_entry("Resume C", 45),
        ]
        msg = format_comparison_summary(entries)
        # Resume B should appear before Resume A before Resume C
        pos_b = msg.index("Resume B")
        pos_a = msg.index("Resume A")
        pos_c = msg.index("Resume C")
        self.assertLess(pos_b, pos_a)
        self.assertLess(pos_a, pos_c)

    def test_best_match_label_shown(self):
        entries = [
            self._make_entry("Winner", 90),
            self._make_entry("Runner Up", 70),
        ]
        msg = format_comparison_summary(entries)
        self.assertIn("Winner", msg)
        self.assertIn("Best match", msg)

    def test_empty_list_returns_message(self):
        msg = format_comparison_summary([])
        self.assertIn("No results", msg)

    def test_single_resume_no_crash(self):
        entries = [self._make_entry("Solo Resume", 55)]
        msg = format_comparison_summary(entries)
        self.assertIn("Solo Resume", msg)


class TestJsonParsing(unittest.TestCase):
    """Tests for the LLM response JSON parsing utilities."""

    def test_clean_json_string_strips_fences(self):
        raw = "```json\n{\"score\": 80}\n```"
        cleaned = _clean_json_string(raw)
        self.assertEqual(cleaned, '{"score": 80}')

    def test_clean_json_string_strips_prose(self):
        raw = "Here is the analysis:\n{\"score\": 72}\nLet me know if you need more."
        cleaned = _clean_json_string(raw)
        self.assertEqual(cleaned, '{"score": 72}')

    def test_parse_valid_json(self):
        raw = json.dumps(SAMPLE_RESULT)
        result = _parse_response(raw)
        self.assertEqual(result["score"], 75)
        self.assertIsInstance(result["strengths"], list)

    def test_parse_with_markdown_fences(self):
        raw = f"```json\n{json.dumps(SAMPLE_RESULT)}\n```"
        result = _parse_response(raw)
        self.assertEqual(result["score"], 75)

    def test_parse_fills_missing_keys(self):
        """Partial response should be filled with defaults, not crash."""
        partial = json.dumps({"score": 55})
        result = _parse_response(partial)
        self.assertEqual(result["score"], 55)
        self.assertEqual(result["strengths"], [])
        self.assertEqual(result["suggestions"], [])

    def test_parse_clamps_score_above_100(self):
        raw = json.dumps({**SAMPLE_RESULT, "score": 150})
        result = _parse_response(raw)
        self.assertEqual(result["score"], 100)

    def test_parse_clamps_score_below_0(self):
        raw = json.dumps({**SAMPLE_RESULT, "score": -10})
        result = _parse_response(raw)
        self.assertEqual(result["score"], 0)

    def test_completely_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            _parse_response("This is not JSON at all and has no braces anywhere.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
