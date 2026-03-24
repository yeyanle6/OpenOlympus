"""Tests for OKR and Decision API endpoints + EventBus broadcasting."""

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from olympus.events.bus import EventBus


@pytest.fixture(autouse=True)
def _reset_eventbus():
    """Ensure a fresh EventBus for each test."""
    EventBus.reset()
    yield
    EventBus.reset()


@pytest.fixture
def client(tmp_path):
    """Create a test client with isolated memory paths."""
    consensus_path = tmp_path / "consensus.md"
    decisions_path = tmp_path / "decisions.jsonl"
    history_dir = tmp_path / "history"

    # Patch shared state before importing app
    import olympus.api.app as app_mod

    app_mod._consensus = app_mod.ConsensusMemory(
        path=consensus_path, history_dir=history_dir
    )
    app_mod._history = app_mod.DecisionHistory(path=decisions_path)

    # Skip loader/director/loop setup
    with patch.object(app_mod._loader, "load_all", return_value={}):
        from fastapi.testclient import TestClient

        yield TestClient(app_mod.app, raise_server_exceptions=True)


# ── OKR GET ──────────────────────────────────────────────────


def test_get_okr_empty(client):
    resp = client.get("/okr")
    assert resp.status_code == 200
    assert resp.json() == {"objectives": []}


def test_get_okr_from_consensus(client, tmp_path):
    """Write OKR markdown to consensus, then GET /okr extracts it."""
    import olympus.api.app as app_mod

    consensus_path = app_mod._consensus.path
    consensus_path.parent.mkdir(parents=True, exist_ok=True)
    consensus_path.write_text(
        "# Consensus\n\n## OKR\n"
        "### O1: Launch MVP\n"
        "- KR1: Ship core features [progress: 0.5]\n"
        "  - I1: Build auth [status: done] @builder\n"
        "- KR2: Pass QA [progress: 0.2]\n"
    )

    resp = client.get("/okr")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["objectives"]) == 1
    obj = data["objectives"][0]
    assert obj["id"] == "O1"
    assert obj["description"] == "Launch MVP"
    assert len(obj["key_results"]) == 2
    assert obj["key_results"][0]["progress"] == 0.5
    assert obj["key_results"][0]["initiatives"][0]["status"] == "done"


# ── OKR POST ─────────────────────────────────────────────────


def test_post_okr(client):
    """POST /okr should write OKR to consensus and broadcast event."""
    handler = AsyncMock()
    EventBus.get().subscribe(handler)

    payload = {
        "objectives": [
            {
                "id": "O1",
                "description": "Ship v1",
                "key_results": [
                    {
                        "id": "KR1",
                        "description": "100 users",
                        "progress": 0.3,
                        "initiatives": [
                            {
                                "id": "I1",
                                "description": "Launch landing page",
                                "status": "in_progress",
                                "owner": "builder",
                            }
                        ],
                    }
                ],
            }
        ]
    }

    resp = client.post("/okr", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["objectives"]) == 1

    # Verify consensus was written
    resp2 = client.get("/okr")
    assert len(resp2.json()["objectives"]) == 1

    # Verify event was broadcast
    assert handler.call_count >= 1
    event = handler.call_args_list[-1].args[0]
    assert event.type == "okr_updated"


def test_post_okr_validation_error(client):
    """POST /okr with invalid data returns 422."""
    payload = {
        "objectives": [
            {
                "id": "INVALID",
                "description": "Bad",
                "key_results": [],
            }
        ]
    }
    resp = client.post("/okr", json=payload)
    assert resp.status_code == 422


def test_post_okr_replaces_existing(client):
    """Posting OKR twice replaces the previous OKR section."""
    payload1 = {
        "objectives": [
            {
                "id": "O1",
                "description": "First",
                "key_results": [{"id": "KR1", "description": "A", "progress": 0.1}],
            }
        ]
    }
    payload2 = {
        "objectives": [
            {
                "id": "O1",
                "description": "Updated",
                "key_results": [{"id": "KR1", "description": "B", "progress": 0.9}],
            }
        ]
    }
    client.post("/okr", json=payload1)
    client.post("/okr", json=payload2)

    resp = client.get("/okr")
    objs = resp.json()["objectives"]
    assert len(objs) == 1
    assert objs[0]["description"] == "Updated"
    assert objs[0]["key_results"][0]["progress"] == 0.9


# ── Decisions POST ───────────────────────────────────────────


def test_post_decision(client):
    """POST /decisions records a decision and broadcasts event."""
    handler = AsyncMock()
    EventBus.get().subscribe(handler)

    payload = {
        "decision": "Proceed with MVP launch",
        "rationale": "Market window closing",
        "decision_type": "go_no_go",
        "confidence": "high",
        "impact_scope": "broad",
    }

    resp = client.post("/decisions", json=payload)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify it was persisted
    resp2 = client.get("/decisions")
    entries = resp2.json()
    assert len(entries) == 1
    assert entries[0]["decision"] == "Proceed with MVP launch"
    assert entries[0]["decision_type"] == "go_no_go"

    # Verify event was broadcast
    assert handler.call_count >= 1
    event = handler.call_args_list[-1].args[0]
    assert event.type == "decision_recorded"
    assert event.data["decision"] == "Proceed with MVP launch"


def test_post_decision_with_metrics(client):
    """POST /decisions with performance metrics."""
    payload = {
        "decision": "Scale up workers",
        "decision_type": "resource_allocation",
        "metrics": {
            "cycle_duration_ms": 5000,
            "cost_usd": 0.12,
            "tokens_used": 4500,
            "tasks_completed": 3,
            "tasks_committed": 4,
        },
        "alternatives": [
            {
                "description": "Keep current scale",
                "rejected_reason": "Too slow",
            }
        ],
    }

    resp = client.post("/decisions", json=payload)
    assert resp.status_code == 200

    entries = client.get("/decisions").json()
    assert entries[0]["metrics"]["tokens_used"] == 4500
    assert entries[0]["alternatives"][0]["rejected_reason"] == "Too slow"


def test_post_decision_invalid_type(client):
    """POST /decisions with invalid decision_type returns 422."""
    payload = {
        "decision": "Bad",
        "decision_type": "not_a_real_type",
    }
    resp = client.post("/decisions", json=payload)
    assert resp.status_code == 422


def test_decisions_search(client):
    """GET /decisions/search filters by keyword."""
    client.post("/decisions", json={"decision": "Launch auth service"})
    client.post("/decisions", json={"decision": "Fix database migration"})
    client.post("/decisions", json={"decision": "Update auth tokens"})

    resp = client.get("/decisions/search", params={"keyword": "auth"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2


# ── Decisions GET (existing) ─────────────────────────────────


def test_get_decisions_empty(client):
    resp = client.get("/decisions")
    assert resp.status_code == 200
    assert resp.json() == []
