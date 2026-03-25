"""Message quality validator — filters out invalid/low-quality agent outputs.

Catches: empty results, tool-only outputs, raw JSON blobs, error messages,
permission denials, truncated outputs, loop artifacts, and repeated content.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    category: str = ""  # empty, tool_only, error, json_blob, truncated, loop, repeated


# Patterns that indicate invalid output
_INVALID_PATTERNS = [
    (re.compile(r"^\[Agent used \d+ tool calls"), "tool_only", "Tool calls without text output"),
    (re.compile(r"^Agent attempted to write \d+ files"), "permission_denied", "Permission denied output"),
    (re.compile(r"^Error:"), "error", "Error message instead of content"),
    (re.compile(r"^Claude CLI"), "error", "CLI error message"),
    (re.compile(r"^AI-SLOP-CLEANER:"), "loop", "Loop detection artifact"),
    (re.compile(r"^Command '.*' timed out"), "error", "Timeout error message"),
]

# Minimum meaningful content length
_MIN_LENGTH = 20


def validate_message(content: str, prev_contents: list[str] | None = None) -> ValidationResult:
    """Validate an agent's output message.

    Args:
        content: The agent's output text
        prev_contents: Previous messages in this discussion (for repeat detection)

    Returns:
        ValidationResult with valid=True if message is acceptable
    """
    if not content or not content.strip():
        return ValidationResult(False, "Empty content", "empty")

    stripped = content.strip()

    # Too short
    if len(stripped) < _MIN_LENGTH:
        return ValidationResult(False, f"Too short ({len(stripped)} chars)", "empty")

    # Known invalid patterns
    for pattern, category, reason in _INVALID_PATTERNS:
        if pattern.search(stripped):
            return ValidationResult(False, reason, category)

    # Raw JSON blob (not human-readable text)
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            # If it parses as JSON with system fields, it's a raw response
            if any(k in parsed for k in ("type", "subtype", "is_error", "usage")):
                return ValidationResult(False, "Raw CLI JSON response", "json_blob")
        except json.JSONDecodeError:
            pass  # Not JSON, might be valid text starting with {

    # Truncated mid-sentence (ends without punctuation and is short)
    if len(stripped) < 50 and not stripped[-1] in ".!?。！？\n}]）":
        return ValidationResult(False, "Truncated output", "truncated")

    # Repeat detection
    if prev_contents:
        for prev in prev_contents[-6:]:  # Check last 6 messages
            if _content_similarity(stripped, prev) > 0.85:
                return ValidationResult(False, "Repeated content (>85% similar)", "repeated")

    return ValidationResult(True)


def _content_similarity(a: str, b: str) -> float:
    """Jaccard similarity on 4+ char keywords."""
    if not a or not b:
        return 0.0
    kw_a = set(re.findall(r'\w{4,}', a.lower()))
    kw_b = set(re.findall(r'\w{4,}', b.lower()))
    if not kw_a or not kw_b:
        return 0.0
    intersection = kw_a & kw_b
    union = kw_a | kw_b
    return len(intersection) / len(union) if union else 0.0
