"""LLM Score Extraction Pipeline.

Reliably converts Critic Agent natural-language evaluations into numeric
scores suitable for entropy calculation.

Three extraction strategies (applied in priority order):
1. Structured JSON block — if the response contains a ```json fence with
   known keys (verdict, scores, confidence), parse it directly.
2. Regex-based extraction — scan for verdict keywords (GO / NO-GO /
   CONDITIONAL-GO), severity markers, and inline numeric ratings.
3. Heuristic fallback — keyword sentiment analysis when no structured
   signal is found.

All scores are normalized to [0.0, 1.0].
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Critic verdict categories mapped to score ranges."""

    GO = "GO"
    CONDITIONAL_GO = "CONDITIONAL-GO"
    NO_GO = "NO-GO"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    REQUEST_CHANGES = "REQUEST-CHANGES"


# Verdict → base score mapping
VERDICT_SCORES: dict[str, float] = {
    "GO": 1.0,
    "APPROVED": 1.0,
    "CONDITIONAL-GO": 0.6,
    "CONDITIONAL_GO": 0.6,
    "REQUEST-CHANGES": 0.3,
    "REQUEST_CHANGES": 0.3,
    "NO-GO": 0.0,
    "NO_GO": 0.0,
    "BLOCKED": 0.0,
}

# Severity keywords → penalty weights
SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 0.3,
    "high": 0.2,
    "warning": 0.1,
    "medium": 0.1,
    "suggestion": 0.02,
    "low": 0.02,
    "note": 0.01,
}

# Positive / negative sentiment keywords for heuristic fallback
_POSITIVE = {
    "strong", "solid", "good", "excellent", "well-designed", "robust",
    "clean", "sound", "viable", "feasible", "recommended", "approve",
}
_NEGATIVE = {
    "weak", "risky", "fragile", "flawed", "dangerous", "unclear",
    "missing", "broken", "incomplete", "blocker", "fatal", "veto",
    "reject", "fail", "concern", "problem", "issue",
}


@dataclass
class DimensionScore:
    """Score for a single evaluation dimension (e.g. feasibility, risk)."""

    name: str
    score: float  # [0.0, 1.0]
    raw: str = ""  # original text or numeric from LLM


@dataclass
class CriticScore:
    """Structured extraction result from a Critic agent's evaluation."""

    verdict: str = ""
    score: float = 0.5  # aggregate normalized score [0.0, 1.0]
    confidence: float = 0.5  # extraction confidence [0.0, 1.0]
    dimensions: list[DimensionScore] = field(default_factory=list)
    severities: dict[str, int] = field(default_factory=dict)  # severity → count
    method: str = ""  # which extraction strategy was used
    raw_text: str = ""

    @property
    def entropy_ready(self) -> bool:
        """Whether this score is reliable enough for entropy calculation."""
        return self.confidence >= 0.3 and self.method != "heuristic"


class ScoreExtractor:
    """Extracts numeric scores from Critic agent natural-language output."""

    # --- Structured JSON extraction ---

    _JSON_BLOCK_RE = re.compile(
        r"```json\s*\n(.*?)\n\s*```", re.DOTALL
    )

    @classmethod
    def _try_structured(cls, text: str) -> CriticScore | None:
        """Attempt to parse a structured JSON scoring block."""
        m = cls._JSON_BLOCK_RE.search(text)
        if not m:
            return None

        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(data, dict):
            return None

        # Must have at least one scoring key
        has_score_key = any(
            k in data for k in ("verdict", "score", "scores", "rating", "confidence")
        )
        if not has_score_key:
            return None

        verdict = str(data.get("verdict", "")).upper().strip()
        confidence = _clamp(float(data.get("confidence", 0.8)), 0.0, 1.0)

        # Extract dimension scores
        dimensions: list[DimensionScore] = []
        scores_data = data.get("scores", data.get("dimensions", {}))
        if isinstance(scores_data, dict):
            for name, val in scores_data.items():
                if isinstance(val, (int, float)):
                    dimensions.append(DimensionScore(
                        name=name,
                        score=_normalize_rating(val),
                        raw=str(val),
                    ))

        # Compute aggregate
        if "score" in data and isinstance(data["score"], (int, float)):
            aggregate = _normalize_rating(data["score"])
        elif dimensions:
            aggregate = sum(d.score for d in dimensions) / len(dimensions)
        elif verdict in VERDICT_SCORES:
            aggregate = VERDICT_SCORES[verdict]
        else:
            aggregate = 0.5

        return CriticScore(
            verdict=verdict,
            score=aggregate,
            confidence=confidence,
            dimensions=dimensions,
            method="structured",
            raw_text=text,
        )

    # --- Regex-based extraction ---

    _VERDICT_RE = re.compile(
        r"\b(NO[_-]GO|CONDITIONAL[_-]GO|GO|APPROVED|BLOCKED|REQUEST[_-]CHANGES)\b",
        re.IGNORECASE,
    )
    _RATING_RE = re.compile(
        r"(?:rating|score|grade)\s*[:=]\s*(\d+(?:\.\d+)?)\s*/?\s*(\d+(?:\.\d+)?)?",
        re.IGNORECASE,
    )
    _CONFIDENCE_RE = re.compile(
        r"confidence\s*[:=]\s*(\d+(?:\.\d+)?)\s*%?",
        re.IGNORECASE,
    )
    _SEVERITY_RE = re.compile(
        r"(?:^|\n)\s*[-*]?\s*\*{0,2}(?:severity\s*[:=]\s*)?\*{0,2}\s*(critical|high|warning|medium|suggestion|low|note)\s*[:—\-]",
        re.IGNORECASE | re.MULTILINE,
    )

    @classmethod
    def _try_regex(cls, text: str) -> CriticScore | None:
        """Extract scores using regex patterns."""
        verdict_match = cls._VERDICT_RE.search(text)
        if not verdict_match:
            return None

        raw_verdict = verdict_match.group(1).upper().replace("_", "-")
        base_score = VERDICT_SCORES.get(raw_verdict, 0.5)

        # Extract inline numeric ratings
        dimensions: list[DimensionScore] = []
        for rm in cls._RATING_RE.finditer(text):
            val = float(rm.group(1))
            max_val = float(rm.group(2)) if rm.group(2) else None
            normalized = _normalize_rating(val, max_val)
            dimensions.append(DimensionScore(
                name="inline_rating",
                score=normalized,
                raw=rm.group(0),
            ))

        # Count severity mentions
        severities: dict[str, int] = {}
        for sm in cls._SEVERITY_RE.finditer(text):
            sev = sm.group(1).lower()
            severities[sev] = severities.get(sev, 0) + 1

        # Apply severity penalty to base score
        penalty = sum(
            SEVERITY_WEIGHTS.get(sev, 0) * count
            for sev, count in severities.items()
        )
        adjusted = max(0.0, base_score - penalty)

        # If we have inline ratings, blend them with verdict
        if dimensions:
            dim_avg = sum(d.score for d in dimensions) / len(dimensions)
            aggregate = 0.6 * adjusted + 0.4 * dim_avg
        else:
            aggregate = adjusted

        # Extract confidence
        conf_match = cls._CONFIDENCE_RE.search(text)
        if conf_match:
            conf_val = float(conf_match.group(1))
            confidence = _clamp(conf_val / 100.0 if conf_val > 1.0 else conf_val, 0.0, 1.0)
        else:
            # Higher confidence if verdict is unambiguous
            confidence = 0.7 if raw_verdict in ("GO", "NO-GO", "APPROVED", "BLOCKED") else 0.5

        return CriticScore(
            verdict=raw_verdict,
            score=_clamp(aggregate, 0.0, 1.0),
            confidence=confidence,
            dimensions=dimensions,
            severities=severities,
            method="regex",
            raw_text=text,
        )

    # --- Heuristic fallback ---

    @classmethod
    def _try_heuristic(cls, text: str) -> CriticScore:
        """Keyword-based sentiment scoring as last resort."""
        words = set(re.findall(r"\b\w[\w-]*\b", text.lower()))
        pos = len(words & _POSITIVE)
        neg = len(words & _NEGATIVE)
        total = pos + neg

        if total == 0:
            score = 0.5
        else:
            score = pos / total

        return CriticScore(
            verdict="",
            score=_clamp(score, 0.0, 1.0),
            confidence=0.2,  # low confidence for heuristic
            method="heuristic",
            raw_text=text,
        )

    # --- Public API ---

    @classmethod
    def extract(cls, text: str) -> CriticScore:
        """Extract a normalized score from Critic agent output.

        Tries strategies in order: structured JSON → regex → heuristic.
        """
        if not text or not text.strip():
            return CriticScore(
                score=0.5,
                confidence=0.0,
                method="empty",
                raw_text=text or "",
            )

        # Strategy 1: Structured JSON
        result = cls._try_structured(text)
        if result is not None:
            return result

        # Strategy 2: Regex
        result = cls._try_regex(text)
        if result is not None:
            return result

        # Strategy 3: Heuristic
        return cls._try_heuristic(text)

    @staticmethod
    def build_scoring_prompt(task: str) -> str:
        """Build a prompt suffix that constrains Critic output to include
        a structured scoring block, making extraction reliable.

        Append this to the Critic's task prompt to get structured output.
        """
        return f"""{task}

## Scoring Output Requirement

After your analysis, you MUST include a structured scoring block in this exact format:

```json
{{
  "verdict": "GO | CONDITIONAL-GO | NO-GO",
  "score": <overall score 0-10>,
  "confidence": <your confidence in this assessment 0.0-1.0>,
  "scores": {{
    "feasibility": <0-10>,
    "risk": <0-10, where 10 = lowest risk>,
    "completeness": <0-10>,
    "alignment": <0-10>
  }},
  "top_risk": "<single sentence describing the biggest risk>",
  "required_mitigations": ["<mitigation 1>", "<mitigation 2>"]
}}
```

Use integer scores 0-10. Include the JSON block even if your verdict is GO."""

    @staticmethod
    def aggregate_scores(scores: list[CriticScore]) -> CriticScore:
        """Combine multiple CriticScores into a confidence-weighted aggregate.

        Useful when multiple critics evaluate the same artifact.
        """
        if not scores:
            return CriticScore(score=0.5, confidence=0.0, method="aggregate")

        if len(scores) == 1:
            return scores[0]

        total_weight = sum(s.confidence for s in scores)
        if total_weight == 0:
            # Equal weight if all confidences are 0
            avg = sum(s.score for s in scores) / len(scores)
            return CriticScore(
                score=avg,
                confidence=0.0,
                method="aggregate",
            )

        weighted_sum = sum(s.score * s.confidence for s in scores)
        aggregate_score = weighted_sum / total_weight
        aggregate_confidence = total_weight / len(scores)

        # Collect all dimensions
        all_dims: dict[str, list[float]] = {}
        for s in scores:
            for d in s.dimensions:
                all_dims.setdefault(d.name, []).append(d.score)
        dimensions = [
            DimensionScore(name=name, score=sum(vals) / len(vals))
            for name, vals in all_dims.items()
        ]

        # Merge severities
        merged_sev: dict[str, int] = {}
        for s in scores:
            for sev, count in s.severities.items():
                merged_sev[sev] = merged_sev.get(sev, 0) + count

        return CriticScore(
            score=_clamp(aggregate_score, 0.0, 1.0),
            confidence=_clamp(aggregate_confidence, 0.0, 1.0),
            dimensions=dimensions,
            severities=merged_sev,
            method="aggregate",
        )

    @staticmethod
    def score_to_entropy(scores: list[float]) -> float:
        """Compute Shannon entropy from a list of normalized scores.

        Treats scores as a probability distribution after normalization.
        Returns entropy in bits. Higher entropy = more disagreement.
        """
        if not scores:
            return 0.0

        # Ensure all values are positive
        shifted = [max(s, 1e-10) for s in scores]
        total = sum(shifted)
        probs = [s / total for s in shifted]

        entropy = -sum(p * math.log2(p) for p in probs if p > 0)
        return entropy


# --- Helpers ---


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize_rating(value: float, max_value: float | None = None) -> float:
    """Normalize a numeric rating to [0.0, 1.0].

    Auto-detects scale:
    - If max_value is given, use it
    - If value > 1.0, assume 0-10 scale
    - If value <= 1.0, assume already normalized
    """
    if max_value is not None and max_value > 0:
        return _clamp(value / max_value, 0.0, 1.0)
    if value > 1.0:
        return _clamp(value / 10.0, 0.0, 1.0)
    return _clamp(value, 0.0, 1.0)
