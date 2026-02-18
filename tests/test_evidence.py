"""Unit tests for app/engine/evidence.py"""

from __future__ import annotations

from app.engine.evidence import (
    _snippet,
    dedupe_evidence,
    find_evidence_for_keywords,
    find_evidence_for_patterns,
    find_evidence_spans,
)
from app.schemas import EvidenceSpan


class TestSnippet:
    """Tests for _snippet helper function."""

    def test_snippet_basic(self):
        text = "Hello World, this is a test string for snippet extraction."
        snippet = _snippet(text, 6, 11, window=5)
        # "World" is at 6-11, window=5 should grab "o World, th"
        assert "World" in snippet

    def test_snippet_at_start(self):
        text = "Start of the text here."
        snippet = _snippet(text, 0, 5, window=10)
        assert "Start" in snippet

    def test_snippet_at_end(self):
        text = "This is a test ending."
        snippet = _snippet(text, 17, 22, window=10)
        assert "ending" in snippet

    def test_snippet_replaces_newlines(self):
        text = "Line1\nLine2\nLine3"
        snippet = _snippet(text, 6, 11, window=20)
        assert "\n" not in snippet
        assert " " in snippet  # newlines replaced with spaces

    def test_snippet_empty_window(self):
        text = "Test"
        snippet = _snippet(text, 0, 4, window=0)
        assert snippet == "Test"


class TestFindEvidenceForKeywords:
    """Tests for find_evidence_for_keywords function."""

    def test_find_single_keyword(self):
        text = "The quick brown fox jumps over the lazy dog."
        result = find_evidence_for_keywords(text, ["fox"])
        assert len(result) == 1
        assert result[0].start_index == 16
        assert result[0].end_index == 19
        assert "fox" in result[0].snippet

    def test_find_multiple_keywords(self):
        text = "The quick brown fox jumps over the lazy dog."
        result = find_evidence_for_keywords(text, ["fox", "dog"])
        assert len(result) == 2

    def test_case_insensitive_search(self):
        text = "The Quick Brown FOX jumps."
        result = find_evidence_for_keywords(text, ["fox"])
        assert len(result) == 1
        assert result[0].start_index == 16

    def test_multiple_occurrences(self):
        text = "cat cat cat"
        result = find_evidence_for_keywords(text, ["cat"])
        assert len(result) == 3

    def test_empty_keyword_skipped(self):
        text = "Test text here."
        result = find_evidence_for_keywords(text, ["", "text"])
        assert len(result) == 1
        assert "text" in result[0].snippet

    def test_no_match_returns_empty(self):
        text = "Hello world"
        result = find_evidence_for_keywords(text, ["xyz"])
        assert result == []

    def test_empty_keywords_list(self):
        text = "Hello world"
        result = find_evidence_for_keywords(text, [])
        assert result == []


class TestFindEvidenceForPatterns:
    """Tests for find_evidence_for_patterns function."""

    def test_find_single_pattern(self):
        text = "Price is 100 dollars."
        result = find_evidence_for_patterns(text, [r"\d+"])
        assert len(result) == 1
        assert "100" in result[0].snippet

    def test_find_multiple_patterns(self):
        text = "Price: 100, Quantity: 50"
        result = find_evidence_for_patterns(text, [r"\d+"])
        assert len(result) == 2

    def test_case_insensitive_pattern(self):
        text = "Hello WORLD world"
        result = find_evidence_for_patterns(text, [r"world"])
        assert len(result) == 2

    def test_empty_pattern_skipped(self):
        text = "Test 123 here"
        result = find_evidence_for_patterns(text, ["", r"\d+"])
        assert len(result) == 1

    def test_no_match_returns_empty(self):
        text = "Hello world"
        result = find_evidence_for_patterns(text, [r"\d+"])
        assert result == []

    def test_complex_pattern(self):
        text = "Email: test@example.com here"
        result = find_evidence_for_patterns(text, [r"[\w]+@[\w]+\.\w+"])
        assert len(result) == 1
        assert "test@example.com" in result[0].snippet


class TestFindEvidenceSpans:
    """Tests for find_evidence_spans function."""

    def test_keywords_only(self):
        text = "The quick brown fox."
        result = find_evidence_spans(text, keywords=["fox"])
        assert len(result) == 1
        assert "fox" in result[0].snippet

    def test_patterns_only(self):
        text = "Price is 100 dollars."
        result = find_evidence_spans(text, patterns=[r"\d+"])
        assert len(result) == 1

    def test_both_keywords_and_patterns(self):
        text = "The fox costs 100 dollars."
        result = find_evidence_spans(text, keywords=["fox"], patterns=[r"\d+"])
        assert len(result) == 2

    def test_max_hits_limit_keywords(self):
        text = "cat cat cat cat cat"
        result = find_evidence_spans(text, keywords=["cat"], max_hits=2)
        assert len(result) == 2

    def test_max_hits_limit_patterns(self):
        text = "1 2 3 4 5 6 7 8 9"
        result = find_evidence_spans(text, patterns=[r"\d"], max_hits=3)
        assert len(result) == 3

    def test_max_hits_across_keywords_and_patterns(self):
        text = "cat 1 cat 2 cat 3"
        result = find_evidence_spans(text, keywords=["cat"], patterns=[r"\d"], max_hits=4)
        # Keywords are processed first, should hit 3 "cat"s, then 1 digit
        assert len(result) == 4

    def test_custom_window_size(self):
        text = "A" * 100 + "TARGET" + "B" * 100
        result = find_evidence_spans(text, keywords=["TARGET"], window=10)
        assert len(result) == 1
        # Snippet should be limited by window
        assert len(result[0].snippet) <= 26  # TARGET(6) + window*2(20)

    def test_none_keywords_and_patterns(self):
        text = "Some text here"
        result = find_evidence_spans(text, keywords=None, patterns=None)
        assert result == []

    def test_empty_keywords_list(self):
        text = "Some text here"
        result = find_evidence_spans(text, keywords=[], patterns=None)
        assert result == []

    def test_empty_keyword_in_list_skipped(self):
        """Empty keyword in list should be skipped (line 69)."""
        text = "The quick brown fox."
        result = find_evidence_spans(text, keywords=["", "fox", ""])
        assert len(result) == 1
        assert "fox" in result[0].snippet

    def test_empty_pattern_in_list_skipped(self):
        """Empty pattern in list should be skipped (line 90)."""
        text = "Price is 100 dollars."
        result = find_evidence_spans(text, patterns=["", r"\d+", ""])
        assert len(result) == 1
        assert "100" in result[0].snippet

    def test_mixed_empty_keywords_and_patterns(self):
        """Both empty keywords and patterns should be skipped."""
        text = "cat costs 100 dollars."
        result = find_evidence_spans(text, keywords=["", "cat", ""], patterns=["", r"\d+", ""])
        assert len(result) == 2


class TestDedupeEvidence:
    """Tests for dedupe_evidence function."""

    def test_no_duplicates(self):
        spans = [
            EvidenceSpan(start_index=0, end_index=5, snippet="hello"),
            EvidenceSpan(start_index=10, end_index=15, snippet="world"),
        ]
        result = dedupe_evidence(spans)
        assert len(result) == 2

    def test_remove_duplicates(self):
        spans = [
            EvidenceSpan(start_index=0, end_index=5, snippet="hello"),
            EvidenceSpan(start_index=0, end_index=5, snippet="hello"),
            EvidenceSpan(start_index=10, end_index=15, snippet="world"),
        ]
        result = dedupe_evidence(spans)
        assert len(result) == 2

    def test_different_snippets_same_indices(self):
        # Same indices but different snippets - should still be deduplicated
        spans = [
            EvidenceSpan(start_index=0, end_index=5, snippet="hello"),
            EvidenceSpan(start_index=0, end_index=5, snippet="HELLO"),
        ]
        result = dedupe_evidence(spans)
        assert len(result) == 1
        assert result[0].snippet == "hello"  # First one kept

    def test_empty_list(self):
        result = dedupe_evidence([])
        assert result == []

    def test_preserves_order(self):
        spans = [
            EvidenceSpan(start_index=20, end_index=25, snippet="third"),
            EvidenceSpan(start_index=0, end_index=5, snippet="first"),
            EvidenceSpan(start_index=10, end_index=15, snippet="second"),
        ]
        result = dedupe_evidence(spans)
        assert result[0].snippet == "third"
        assert result[1].snippet == "first"
        assert result[2].snippet == "second"

    def test_multiple_duplicates(self):
        spans = [
            EvidenceSpan(start_index=0, end_index=5, snippet="a"),
            EvidenceSpan(start_index=0, end_index=5, snippet="b"),
            EvidenceSpan(start_index=0, end_index=5, snippet="c"),
            EvidenceSpan(start_index=10, end_index=15, snippet="d"),
            EvidenceSpan(start_index=10, end_index=15, snippet="e"),
        ]
        result = dedupe_evidence(spans)
        assert len(result) == 2
        assert result[0].snippet == "a"
        assert result[1].snippet == "d"
