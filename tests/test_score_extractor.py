"""Tests for the LLM Score Extraction Pipeline."""

import math
import pytest

from olympus.data.score_extractor import (
    CriticScore,
    DimensionScore,
    ScoreExtractor,
    Verdict,
    VERDICT_SCORES,
    _clamp,
    _normalize_rating,
)


# ---------------------------------------------------------------------------
# Structured JSON extraction
# ---------------------------------------------------------------------------

class TestStructuredExtraction:
    """Strategy 1: parse ```json blocks."""

    def test_full_structured_block(self):
        text = """
Pre-mortem analysis complete.

```json
{
  "verdict": "CONDITIONAL-GO",
  "score": 7,
  "confidence": 0.85,
  "scores": {
    "feasibility": 8,
    "risk": 5,
    "completeness": 7,
    "alignment": 9
  },
  "top_risk": "Database migration may fail under load",
  "required_mitigations": ["Add rollback plan"]
}
```

Overall the proposal is viable with mitigations.
"""
        result = ScoreExtractor.extract(text)
        assert result.method == "structured"
        assert result.verdict == "CONDITIONAL-GO"
        assert result.score == pytest.approx(0.7, abs=0.01)  # 7/10
        assert result.confidence == pytest.approx(0.85)
        assert len(result.dimensions) == 4
        assert result.entropy_ready is True

    def test_structured_go_verdict(self):
        text = '```json\n{"verdict": "GO", "score": 9, "confidence": 0.95}\n```'
        result = ScoreExtractor.extract(text)
        assert result.method == "structured"
        assert result.verdict == "GO"
        assert result.score == pytest.approx(0.9)
        assert result.confidence == pytest.approx(0.95)

    def test_structured_no_go(self):
        text = '```json\n{"verdict": "NO-GO", "score": 2, "confidence": 0.9}\n```'
        result = ScoreExtractor.extract(text)
        assert result.verdict == "NO-GO"
        assert result.score == pytest.approx(0.2)

    def test_structured_dimension_average(self):
        """When no explicit score, average dimensions."""
        text = '```json\n{"verdict": "GO", "scores": {"a": 8, "b": 6}}\n```'
        result = ScoreExtractor.extract(text)
        assert result.score == pytest.approx(0.7)  # (0.8+0.6)/2

    def test_structured_verdict_fallback(self):
        """When no score or dimensions, use verdict mapping."""
        text = '```json\n{"verdict": "GO"}\n```'
        result = ScoreExtractor.extract(text)
        assert result.score == pytest.approx(1.0)

    def test_invalid_json_falls_through(self):
        text = '```json\n{not valid json}\n```\nVerdict: GO'
        result = ScoreExtractor.extract(text)
        # Should fall through to regex
        assert result.method == "regex"

    def test_json_without_score_keys_falls_through(self):
        text = '```json\n{"unrelated": "data"}\n```\nVerdict: GO'
        result = ScoreExtractor.extract(text)
        assert result.method == "regex"


# ---------------------------------------------------------------------------
# Regex-based extraction
# ---------------------------------------------------------------------------

class TestRegexExtraction:
    """Strategy 2: regex patterns for verdicts, ratings, severity."""

    def test_simple_go(self):
        text = "After thorough analysis, my verdict is: GO. The plan is sound."
        result = ScoreExtractor.extract(text)
        assert result.method == "regex"
        assert result.verdict == "GO"
        assert result.score == pytest.approx(1.0)
        assert result.confidence == pytest.approx(0.7)

    def test_no_go_verdict(self):
        text = "Verdict: NO-GO. Fatal flaw in the authentication design."
        result = ScoreExtractor.extract(text)
        assert result.verdict == "NO-GO"
        assert result.score == pytest.approx(0.0)

    def test_conditional_go(self):
        text = "CONDITIONAL-GO — proceed only after addressing security concerns."
        result = ScoreExtractor.extract(text)
        assert result.verdict == "CONDITIONAL-GO"
        assert result.score == pytest.approx(0.6)
        assert result.confidence == pytest.approx(0.5)

    def test_approved_verdict(self):
        text = "Code review complete. [APPROVED] — well structured."
        result = ScoreExtractor.extract(text)
        assert result.verdict == "APPROVED"
        assert result.score == pytest.approx(1.0)

    def test_blocked_verdict(self):
        text = "Review: [BLOCKED] — security vulnerability found."
        result = ScoreExtractor.extract(text)
        assert result.verdict == "BLOCKED"
        assert result.score == pytest.approx(0.0)

    def test_severity_penalty(self):
        text = "Verdict: GO\n- critical: SQL injection\n- warning: missing tests"
        result = ScoreExtractor.extract(text)
        assert result.verdict == "GO"
        # 1.0 - 0.3 (critical) - 0.1 (warning) = 0.6
        assert result.score == pytest.approx(0.6, abs=0.01)
        assert result.severities["critical"] == 1
        assert result.severities["warning"] == 1

    def test_multiple_severities(self):
        text = "GO\n- critical: A\n- critical: B\n- warning: C"
        result = ScoreExtractor.extract(text)
        # 1.0 - 0.3*2 - 0.1 = 0.3
        assert result.score == pytest.approx(0.3, abs=0.01)

    def test_inline_rating(self):
        text = "Verdict: CONDITIONAL-GO\nRating: 7/10"
        result = ScoreExtractor.extract(text)
        # 0.6*adjusted + 0.4*0.7 = 0.6*0.6 + 0.4*0.7 = 0.36+0.28 = 0.64
        assert result.score == pytest.approx(0.64, abs=0.01)

    def test_confidence_extraction(self):
        text = "Verdict: GO\nConfidence: 90%"
        result = ScoreExtractor.extract(text)
        assert result.confidence == pytest.approx(0.9)

    def test_confidence_decimal(self):
        text = "Verdict: GO\nconfidence: 0.75"
        result = ScoreExtractor.extract(text)
        assert result.confidence == pytest.approx(0.75)

    def test_request_changes(self):
        text = "After review: REQUEST-CHANGES. Several issues need fixing."
        result = ScoreExtractor.extract(text)
        assert result.verdict == "REQUEST-CHANGES"
        assert result.score == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

class TestHeuristicExtraction:
    """Strategy 3: keyword sentiment when no verdict found."""

    def test_positive_text(self):
        text = "The design is solid and robust. Clean architecture with sound principles."
        result = ScoreExtractor.extract(text)
        assert result.method == "heuristic"
        assert result.score > 0.5
        assert result.confidence == pytest.approx(0.2)
        assert result.entropy_ready is False

    def test_negative_text(self):
        text = "This is risky and fragile. There are missing pieces and a fatal flaw."
        result = ScoreExtractor.extract(text)
        assert result.method == "heuristic"
        assert result.score < 0.5

    def test_neutral_text(self):
        text = "The implementation uses standard patterns."
        result = ScoreExtractor.extract(text)
        assert result.method == "heuristic"
        assert result.score == pytest.approx(0.5)

    def test_empty_input(self):
        result = ScoreExtractor.extract("")
        assert result.method == "empty"
        assert result.score == pytest.approx(0.5)
        assert result.confidence == pytest.approx(0.0)

    def test_none_input(self):
        result = ScoreExtractor.extract(None)  # type: ignore
        assert result.method == "empty"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_single_score(self):
        s = CriticScore(score=0.8, confidence=0.9, method="regex")
        result = ScoreExtractor.aggregate_scores([s])
        assert result is s  # identity for single

    def test_empty_list(self):
        result = ScoreExtractor.aggregate_scores([])
        assert result.score == pytest.approx(0.5)
        assert result.confidence == pytest.approx(0.0)

    def test_confidence_weighted(self):
        s1 = CriticScore(score=1.0, confidence=0.9, method="structured")
        s2 = CriticScore(score=0.0, confidence=0.1, method="heuristic")
        result = ScoreExtractor.aggregate_scores([s1, s2])
        # Weighted: (1.0*0.9 + 0.0*0.1) / (0.9+0.1) = 0.9
        assert result.score == pytest.approx(0.9)
        assert result.method == "aggregate"

    def test_equal_confidence(self):
        s1 = CriticScore(score=0.8, confidence=0.5, method="regex")
        s2 = CriticScore(score=0.4, confidence=0.5, method="regex")
        result = ScoreExtractor.aggregate_scores([s1, s2])
        assert result.score == pytest.approx(0.6)

    def test_zero_confidence_equal_weight(self):
        s1 = CriticScore(score=0.8, confidence=0.0, method="heuristic")
        s2 = CriticScore(score=0.2, confidence=0.0, method="heuristic")
        result = ScoreExtractor.aggregate_scores([s1, s2])
        assert result.score == pytest.approx(0.5)

    def test_dimension_merging(self):
        s1 = CriticScore(
            score=0.8, confidence=0.5,
            dimensions=[DimensionScore(name="risk", score=0.6)],
        )
        s2 = CriticScore(
            score=0.6, confidence=0.5,
            dimensions=[DimensionScore(name="risk", score=0.4)],
        )
        result = ScoreExtractor.aggregate_scores([s1, s2])
        assert len(result.dimensions) == 1
        assert result.dimensions[0].name == "risk"
        assert result.dimensions[0].score == pytest.approx(0.5)

    def test_severity_merging(self):
        s1 = CriticScore(score=0.5, confidence=0.5, severities={"critical": 1})
        s2 = CriticScore(score=0.5, confidence=0.5, severities={"critical": 2, "warning": 1})
        result = ScoreExtractor.aggregate_scores([s1, s2])
        assert result.severities == {"critical": 3, "warning": 1}


# ---------------------------------------------------------------------------
# Entropy
# ---------------------------------------------------------------------------

class TestEntropy:
    def test_uniform_scores(self):
        """Uniform distribution → maximum entropy."""
        entropy = ScoreExtractor.score_to_entropy([0.5, 0.5, 0.5, 0.5])
        assert entropy == pytest.approx(2.0)  # log2(4) = 2

    def test_single_score(self):
        entropy = ScoreExtractor.score_to_entropy([1.0])
        assert entropy == pytest.approx(0.0)  # single element → 0 entropy

    def test_empty_list(self):
        assert ScoreExtractor.score_to_entropy([]) == 0.0

    def test_polarized_scores(self):
        """One high, rest near-zero → low entropy."""
        entropy = ScoreExtractor.score_to_entropy([1.0, 0.001, 0.001])
        assert entropy < 0.5  # very low

    def test_disagreement_higher_entropy(self):
        """Spread-out distribution → higher entropy than concentrated."""
        concentrated = ScoreExtractor.score_to_entropy([0.95, 0.05])
        spread = ScoreExtractor.score_to_entropy([0.5, 0.5])
        assert spread > concentrated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_clamp(self):
        assert _clamp(0.5, 0.0, 1.0) == 0.5
        assert _clamp(-1.0, 0.0, 1.0) == 0.0
        assert _clamp(2.0, 0.0, 1.0) == 1.0

    def test_normalize_rating_with_max(self):
        assert _normalize_rating(7, 10) == pytest.approx(0.7)
        assert _normalize_rating(3, 5) == pytest.approx(0.6)

    def test_normalize_rating_auto_detect_10_scale(self):
        assert _normalize_rating(8) == pytest.approx(0.8)
        assert _normalize_rating(10) == pytest.approx(1.0)

    def test_normalize_rating_already_normalized(self):
        assert _normalize_rating(0.7) == pytest.approx(0.7)

    def test_normalize_rating_clamps(self):
        assert _normalize_rating(15, 10) == 1.0
        assert _normalize_rating(-1, 10) == 0.0


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

class TestPromptBuilder:
    def test_builds_prompt_with_json_template(self):
        prompt = ScoreExtractor.build_scoring_prompt("Review this design")
        assert "Review this design" in prompt
        assert "```json" in prompt
        assert '"verdict"' in prompt
        assert '"scores"' in prompt
        assert '"confidence"' in prompt

    def test_prompt_includes_all_dimensions(self):
        prompt = ScoreExtractor.build_scoring_prompt("test")
        for dim in ("feasibility", "risk", "completeness", "alignment"):
            assert dim in prompt


# ---------------------------------------------------------------------------
# CriticScore.entropy_ready
# ---------------------------------------------------------------------------

class TestEntropyReady:
    def test_structured_high_confidence(self):
        s = CriticScore(confidence=0.8, method="structured")
        assert s.entropy_ready is True

    def test_regex_medium_confidence(self):
        s = CriticScore(confidence=0.5, method="regex")
        assert s.entropy_ready is True

    def test_heuristic_never_ready(self):
        s = CriticScore(confidence=0.5, method="heuristic")
        assert s.entropy_ready is False

    def test_low_confidence_not_ready(self):
        s = CriticScore(confidence=0.1, method="structured")
        assert s.entropy_ready is False


# ---------------------------------------------------------------------------
# Integration: realistic Critic output
# ---------------------------------------------------------------------------

class TestRealisticCriticOutput:
    def test_munger_style_review(self):
        text = """
## Pre-Mortem Analysis

If this project failed in 6 months, it would be because:
1. The scoring pipeline introduces latency in the critical path
2. LLM output format drift causes silent extraction failures

## Bias Check
- Confirmation bias: Team may be overly optimistic about LLM output consistency ⚠️
- Sunk cost: Moderate risk — existing heuristic system works, switching has cost
- Authority bias: Low risk
- Availability bias: Low risk
- Social proof: Low risk
- Incentive bias: Low risk

## Top Risk
**Severity: warning** — LLM output format is not guaranteed stable across model versions

## Verdict: CONDITIONAL-GO

Required mitigations:
1. Add fallback extraction strategies (done in implementation)
2. Monitor extraction confidence over time
3. Alert when confidence drops below threshold

Confidence: 75%
"""
        result = ScoreExtractor.extract(text)
        assert result.verdict == "CONDITIONAL-GO"
        assert result.method == "regex"
        assert result.confidence == pytest.approx(0.75)
        assert 0.3 <= result.score <= 0.7
        assert result.entropy_ready is True

    def test_structured_reviewer_output(self):
        text = """
Code review complete.

```json
{
  "verdict": "APPROVED",
  "score": 8,
  "confidence": 0.9,
  "scores": {
    "code_quality": 8,
    "test_coverage": 7,
    "security": 9,
    "performance": 8
  },
  "top_risk": "No edge case tests for malformed JSON input"
}
```

[APPROVED] — clean implementation with good test coverage.
"""
        result = ScoreExtractor.extract(text)
        assert result.method == "structured"
        assert result.verdict == "APPROVED"
        assert result.score == pytest.approx(0.8)
        assert len(result.dimensions) == 4
