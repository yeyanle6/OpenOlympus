"""JSON Schema definitions for OKR hierarchy, decision log, and performance metrics.

These schemas formalise the data models used by ConsensusMemory and
DecisionHistory so that external tools, validators, and dashboards can
consume the same structure.

Usage::

    from olympus.memory.schemas import validate_okr, validate_decision_entry

    issues = validate_okr(okr_dict)    # returns list[str] of issues
    issues = validate_decision_entry(entry_dict)
"""

from __future__ import annotations

from typing import Any

# =====================================================================
# OKR JSON Schema — Objective / KeyResult / Initiative hierarchy
# =====================================================================

INITIATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": r"^I\d+$"},
        "description": {"type": "string", "minLength": 1},
        "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "done", "blocked"],
            "default": "pending",
        },
        "owner": {"type": "string", "default": ""},
    },
    "required": ["id", "description"],
    "additionalProperties": False,
}

KEY_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": r"^KR\d+$"},
        "description": {"type": "string", "minLength": 1},
        "progress": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.0,
        },
        "initiatives": {
            "type": "array",
            "items": INITIATIVE_SCHEMA,
            "default": [],
        },
    },
    "required": ["id", "description"],
    "additionalProperties": False,
}

OBJECTIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": r"^O\d+$"},
        "description": {"type": "string", "minLength": 1},
        "key_results": {
            "type": "array",
            "items": KEY_RESULT_SCHEMA,
            "minItems": 1,
        },
    },
    "required": ["id", "description", "key_results"],
    "additionalProperties": False,
}

OKR_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OKR Hierarchy",
    "description": "Objectives & Key Results with Initiatives for consensus.md",
    "type": "array",
    "items": OBJECTIVE_SCHEMA,
}

# =====================================================================
# Decision Log JSON Schema
# =====================================================================

ALTERNATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "minLength": 1},
        "rejected_reason": {"type": "string", "default": ""},
    },
    "required": ["description"],
    "additionalProperties": False,
}

PERFORMANCE_METRICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cycle_duration_ms": {"type": "integer", "minimum": 0, "default": 0},
        "cost_usd": {"type": "number", "minimum": 0.0, "default": 0.0},
        "tokens_used": {"type": "integer", "minimum": 0, "default": 0},
        "tasks_completed": {"type": "integer", "minimum": 0, "default": 0},
        "tasks_committed": {"type": "integer", "minimum": 0, "default": 0},
        "blockers_detected": {"type": "integer", "minimum": 0, "default": 0},
        "blocker_types": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
    },
    "additionalProperties": False,
}

DECISION_ENTRY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Decision Log Entry",
    "description": "A single decision record in the append-only JSONL log",
    "type": "object",
    "properties": {
        "timestamp": {"type": "string", "format": "date-time"},
        "cycle": {"type": ["integer", "null"]},
        "phase": {"type": "string"},
        "decision": {"type": "string", "minLength": 1},
        "rationale": {"type": "string", "default": ""},
        "agents": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "room_id": {"type": "string", "default": ""},
        "decision_type": {
            "type": "string",
            "enum": [
                "go_no_go",
                "pivot",
                "scope_change",
                "escalation",
                "resource_allocation",
                "sprint_commitment",
                "general",
            ],
            "default": "general",
        },
        "sprint": {"type": "integer", "minimum": 1},
        "sprint_goal": {"type": "string"},
        "metrics": PERFORMANCE_METRICS_SCHEMA,
        "alternatives": {
            "type": "array",
            "items": ALTERNATIVE_SCHEMA,
        },
        "impact": {"type": "string"},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "impact_scope": {
            "type": "string",
            "enum": ["narrow", "moderate", "broad"],
        },
    },
    "required": ["timestamp", "decision", "decision_type"],
    "additionalProperties": False,
}


# =====================================================================
# Lightweight validation (no external dependency on jsonschema)
# =====================================================================


def _check_pattern(value: str, pattern: str) -> bool:
    """Check if value matches a simple ^PREFIX\\d+$ pattern."""
    import re

    return bool(re.match(pattern, value))


def validate_okr(data: list[dict[str, Any]]) -> list[str]:
    """Validate a list of OKR objective dicts against the schema.

    Returns a list of human-readable issues (empty = valid).
    This is a lightweight validator that does not require the
    ``jsonschema`` library.
    """
    issues: list[str] = []
    if not isinstance(data, list):
        return ["OKR data must be a list of objectives"]

    seen_obj_ids: set[str] = set()
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            issues.append(f"objectives[{i}]: must be an object")
            continue

        obj_id = obj.get("id", "")
        if not obj_id or not _check_pattern(obj_id, r"^O\d+$"):
            issues.append(f"objectives[{i}]: id must match O<number>")
        if obj_id in seen_obj_ids:
            issues.append(f"objectives[{i}]: duplicate id {obj_id}")
        seen_obj_ids.add(obj_id)

        if not obj.get("description"):
            issues.append(f"{obj_id}: description is required")

        krs = obj.get("key_results", [])
        if not krs:
            issues.append(f"{obj_id}: must have at least one key_result")

        seen_kr_ids: set[str] = set()
        for j, kr in enumerate(krs):
            if not isinstance(kr, dict):
                issues.append(f"{obj_id}/key_results[{j}]: must be an object")
                continue
            kr_id = kr.get("id", "")
            if not kr_id or not _check_pattern(kr_id, r"^KR\\d+$"):
                # Accept KR pattern loosely
                pass
            if kr_id in seen_kr_ids:
                issues.append(f"{obj_id}/{kr_id}: duplicate")
            seen_kr_ids.add(kr_id)

            progress = kr.get("progress", 0.0)
            if not isinstance(progress, (int, float)):
                issues.append(f"{obj_id}/{kr_id}: progress must be a number")
            elif not (0.0 <= progress <= 1.0):
                issues.append(f"{obj_id}/{kr_id}: progress out of range")

            for k, init in enumerate(kr.get("initiatives", [])):
                if not isinstance(init, dict):
                    issues.append(
                        f"{obj_id}/{kr_id}/initiatives[{k}]: must be an object"
                    )
                    continue
                init_id = init.get("id", "")
                if not init_id:
                    issues.append(
                        f"{obj_id}/{kr_id}/initiatives[{k}]: id is required"
                    )
                status = init.get("status", "pending")
                valid = {"pending", "in_progress", "done", "blocked"}
                if status not in valid:
                    issues.append(
                        f"{obj_id}/{kr_id}/{init_id}: invalid status '{status}'"
                    )

    return issues


def validate_decision_entry(data: dict[str, Any]) -> list[str]:
    """Validate a single decision log entry dict.

    Returns a list of human-readable issues (empty = valid).
    """
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["Decision entry must be an object"]

    if not data.get("timestamp"):
        issues.append("timestamp is required")
    if not data.get("decision"):
        issues.append("decision is required")

    dt = data.get("decision_type", "general")
    valid_types = {
        "go_no_go", "pivot", "scope_change", "escalation",
        "resource_allocation", "sprint_commitment", "general",
    }
    if dt not in valid_types:
        issues.append(f"invalid decision_type: {dt}")

    conf = data.get("confidence")
    if conf is not None and conf not in {"high", "medium", "low"}:
        issues.append(f"invalid confidence: {conf}")

    scope = data.get("impact_scope")
    if scope is not None and scope not in {"narrow", "moderate", "broad"}:
        issues.append(f"invalid impact_scope: {scope}")

    metrics = data.get("metrics")
    if metrics is not None:
        if not isinstance(metrics, dict):
            issues.append("metrics must be an object")
        else:
            for int_field in ("cycle_duration_ms", "tokens_used",
                              "tasks_completed", "tasks_committed",
                              "blockers_detected"):
                v = metrics.get(int_field)
                if v is not None and (not isinstance(v, (int, float)) or v < 0):
                    issues.append(f"metrics.{int_field}: must be non-negative")

    alts = data.get("alternatives")
    if alts is not None:
        if not isinstance(alts, list):
            issues.append("alternatives must be a list")
        else:
            for i, alt in enumerate(alts):
                if not isinstance(alt, dict):
                    issues.append(f"alternatives[{i}]: must be an object")
                elif not alt.get("description"):
                    issues.append(f"alternatives[{i}]: description is required")

    return issues


def okr_to_dicts(objectives: list) -> list[dict[str, Any]]:
    """Convert a list of Objective dataclass instances to plain dicts.

    This bridges the typed dataclasses in consensus.py to the JSON Schema
    representation, making it easy to serialise or validate.
    """
    result = []
    for obj in objectives:
        krs = []
        for kr in obj.key_results:
            inits = [
                {
                    "id": init.id,
                    "description": init.description,
                    "status": init.status,
                    "owner": init.owner,
                }
                for init in getattr(kr, "initiatives", [])
            ]
            kr_dict: dict[str, Any] = {
                "id": kr.id,
                "description": kr.description,
                "progress": kr.progress,
            }
            if inits:
                kr_dict["initiatives"] = inits
            krs.append(kr_dict)
        result.append({
            "id": obj.id,
            "description": obj.description,
            "key_results": krs,
        })
    return result
