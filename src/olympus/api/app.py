"""FastAPI application — REST + WebSocket entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from olympus.types import RoomStatus
from olympus.agent.speaker import SpeakerLock
from olympus.agent.loader import AgentLoader
from olympus.agent.pool import AgentPool
from olympus.api.ws import ConnectionManager
from olympus.director.director import Director
from olympus.events.bus import EventBus
from olympus.events.types import Event
from olympus.loop.engine import LoopEngine, LoopConfig
from olympus.memory.consensus import ConsensusMemory
from olympus.memory.history import DecisionHistory, DecisionType, Confidence, ImpactScope, Alternative, PerformanceMetrics
from olympus.memory.schemas import validate_okr, validate_decision_entry, okr_to_dicts
from olympus.data.database import Database
from olympus.data.collector import CycleMetricsCollector, GitCollector, OkrCollector
from olympus.data.rules import RuleEngine

# ── Shared state ──────────────────────────────────────────────

_loader = AgentLoader()
_pool = AgentPool(_loader)
_consensus = ConsensusMemory()
_history = DecisionHistory()
_director = Director(_loader, _pool, _consensus)
_loop_engine = LoopEngine(consensus=_consensus, history=_history)
_ws_manager = ConnectionManager()
_loop_task: asyncio.Task | None = None
_db = Database()
_cycle_collector = CycleMetricsCollector(_db)
_git_collector = GitCollector(_db)
_okr_collector = OkrCollector(_db)
_rule_engine = RuleEngine(_db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _loader.load_all()
    _ws_manager.setup()
    restored = await _director.restore_from_disk()
    if restored:
        import logging
        logging.getLogger(__name__).info("Restored %d rooms from disk", restored)
    # Start data pipeline
    await _db.init()
    await _cycle_collector.start()
    await _git_collector.start()
    await _okr_collector.start()
    await _rule_engine.start()
    yield
    # Shutdown data pipeline
    await _rule_engine.stop()
    await _okr_collector.stop()
    await _git_collector.stop()
    await _cycle_collector.stop()
    await _db.close()
    await _director.shutdown()
    _loop_engine.stop()


app = FastAPI(title="OpenOlympus", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    room_id: str = ""
    protocol: str = ""
    agents: list[str] = []
    rooms: list[dict[str, Any]] = []


# ── OKR models ───────────────────────────────────────────────

class InitiativeIn(BaseModel):
    id: str
    description: str
    status: str = "pending"
    owner: str = ""


class KeyResultIn(BaseModel):
    id: str
    description: str
    progress: float = 0.0
    initiatives: list[InitiativeIn] = []


class ObjectiveIn(BaseModel):
    id: str
    description: str
    key_results: list[KeyResultIn]


class OKRRequest(BaseModel):
    objectives: list[ObjectiveIn]


class OKRResponse(BaseModel):
    objectives: list[dict[str, Any]]


# ── Decision models ──────────────────────────────────────────

class AlternativeIn(BaseModel):
    description: str
    rejected_reason: str = ""


class PerformanceMetricsIn(BaseModel):
    cycle_duration_ms: int = 0
    cost_usd: float = 0.0
    tokens_used: int = 0
    tasks_completed: int = 0
    tasks_committed: int = 0
    blockers_detected: int = 0
    blocker_types: list[str] = []


class DecisionRequest(BaseModel):
    decision: str
    rationale: str = ""
    cycle: int | None = None
    phase: str = ""
    agents: list[str] = []
    room_id: str = ""
    decision_type: str = "general"
    sprint: int | None = None
    sprint_goal: str = ""
    metrics: PerformanceMetricsIn | None = None
    alternatives: list[AlternativeIn] = []
    impact: str = ""
    confidence: str | None = None
    impact_scope: str | None = None


# ── Health ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/speaker")
async def speaker_status():
    return SpeakerLock.get().status()


@app.get("/tools")
async def list_custom_tools():
    from olympus.agent.llm_agent import get_evolution_engine
    return get_evolution_engine().list_tools()


# ── Agents ────────────────────────────────────────────────────

@app.get("/agents")
async def list_agents():
    agents = _loader.load_all()
    return [
        {
            "id": a.agent_id,
            "name": a.name,
            "description": a.description,
            "layer": a.layer.value,
            "capabilities": a.capabilities,
            "permissions": {
                "write": a.effective_permissions.write,
                "execute": a.effective_permissions.execute,
                "spawn_rooms": a.effective_permissions.spawn_rooms,
            },
        }
        for a in agents.values()
    ]


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    a = _loader.get(agent_id)
    if not a:
        return {"error": "Agent not found"}
    return {
        "id": a.agent_id,
        "name": a.name,
        "description": a.description,
        "layer": a.layer.value,
        "capabilities": a.capabilities,
        "persona": a.persona,
        "principles": a.principles,
    }


# ── Director ──────────────────────────────────────────────────

@app.post("/director/chat", response_model=ChatResponse)
async def director_chat(req: ChatRequest):
    result = await _director.chat(req.message)
    return ChatResponse(
        reply=result.get("reply", ""),
        room_id=result.get("room_id", ""),
        protocol=result.get("protocol", ""),
        agents=result.get("agents", []),
        rooms=result.get("rooms", []),
    )


# ── Rooms ─────────────────────────────────────────────────────

@app.get("/rooms")
async def list_rooms():
    return await _director.get_rooms_status()


@app.get("/rooms/{room_id}")
async def get_room(room_id: str):
    rooms = await _director.get_rooms_status()
    for r in rooms:
        if r["room_id"] == room_id:
            return r
    return {"error": "Room not found"}


@app.get("/rooms/{room_id}/messages")
async def get_room_messages(room_id: str):
    return _director.get_room_messages(room_id)


@app.get("/rooms/{room_id}/export")
async def export_room(room_id: str):
    """Export room discussion as markdown."""
    from fastapi.responses import PlainTextResponse
    msgs = _director.get_room_messages(room_id)
    rooms = await _director.get_rooms_status()
    room = next((r for r in rooms if r["room_id"] == room_id), None)
    if not room:
        return PlainTextResponse("Room not found", status_code=404)

    lines = [
        f"# {room.get('task', 'Discussion')[:80]}",
        f"",
        f"**Room**: {room_id}",
        f"**Protocol**: {room.get('protocol', '')}",
        f"**Agents**: {', '.join(room.get('agents', []))}",
        f"**Status**: {room.get('status', '')}",
        f"**Messages**: {len(msgs)}",
        "", "---", "",
    ]
    for i, m in enumerate(msgs):
        lines.append(f"### {m['sender'].upper()} (msg {i+1})")
        lines.append("")
        lines.append(m["content"])
        lines.append("")
        lines.append("---")
        lines.append("")

    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=room-{room_id[:8]}.md"},
    )


@app.get("/rooms/{room_id}/wbs")
async def get_room_wbs(room_id: str):
    return _director.get_wbs(room_id)


@app.get("/rooms/{room_id}/references")
async def get_room_references(room_id: str):
    return _director.get_room_references(room_id)


@app.post("/rooms/{room_id}/pause")
async def pause_room(room_id: str):
    gate = _director._gates.get(room_id)
    if not gate:
        return {"ok": False, "reply": "Room not found"}
    gate.pause()
    managed = _director._rooms.get(room_id)
    if managed:
        managed.status = RoomStatus.PAUSED
    return {"ok": True, "reply": f"Room {room_id} paused"}


@app.post("/rooms/{room_id}/resume")
async def resume_room(room_id: str):
    gate = _director._gates.get(room_id)
    if not gate:
        return {"ok": False, "reply": "Room not found"}
    gate.resume()
    managed = _director._rooms.get(room_id)
    if managed:
        managed.status = RoomStatus.RUNNING
    return {"ok": True, "reply": f"Room {room_id} resumed"}


@app.post("/rooms/{room_id}/stop")
async def stop_room(room_id: str):
    gate = _director._gates.get(room_id)
    if not gate:
        return {"ok": False, "reply": "Room not found"}
    gate.cancel()
    return {"ok": True, "reply": f"Room {room_id} stopped"}


class InjectRequest(BaseModel):
    content: str
    sender: str = "user"


@app.post("/rooms/{room_id}/inject")
async def inject_message(room_id: str, req: InjectRequest):
    """Inject a user message into a running room's context."""
    msg_data = {"sender": req.sender, "content": req.content, "type": "system"}
    if room_id not in _director._room_messages:
        _director._room_messages[room_id] = []
    _director._room_messages[room_id].append(msg_data)
    # Broadcast via WebSocket so UI sees it
    EventBus.get().publish_nowait(Event(
        type="room_message",
        room_id=room_id,
        data=msg_data,
    ))
    return {"ok": True, "reply": f"Message injected into room {room_id}"}


# ── Loop ──────────────────────────────────────────────────────

@app.get("/loop/status")
async def loop_status():
    return {
        "status": _loop_engine.state.status,
        "cycle_count": _loop_engine.state.cycle_count,
        "error_count": _loop_engine.state.error_count,
        "last_cost": _loop_engine.state.last_cost,
        "total_cost": _loop_engine.state.total_cost,
    }


@app.post("/loop/start")
async def loop_start():
    global _loop_task
    if _loop_engine.state.status == "running":
        return {"ok": False, "reason": "Already running"}
    _loop_task = asyncio.create_task(_loop_engine.start())
    return {"ok": True}


@app.post("/loop/stop")
async def loop_stop():
    _loop_engine.stop()
    return {"ok": True}


@app.get("/loop/cycles")
async def loop_cycles(limit: int = 20):
    decisions = await _history.get_recent(limit)
    return decisions


# ── Memory ────────────────────────────────────────────────────

@app.get("/consensus")
async def get_consensus():
    content = await _consensus.read()
    return {"content": content}


@app.get("/consensus/history")
async def consensus_history(limit: int = 10):
    history = await _consensus.get_history(limit)
    return [{"timestamp": ts, "content": content} for ts, content in history]


@app.get("/decisions")
async def list_decisions(limit: int = 20):
    return await _history.get_recent(limit)


@app.get("/decisions/search")
async def search_decisions(keyword: str, limit: int = 10):
    return await _history.search(keyword, limit)


@app.post("/decisions")
async def record_decision(req: DecisionRequest):
    """Record a new decision and broadcast a decision_recorded event."""
    # Validate decision_type, confidence, impact_scope enums
    valid_types = {"go_no_go", "pivot", "scope_change", "escalation",
                   "resource_allocation", "sprint_commitment", "general"}
    if req.decision_type not in valid_types:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=[f"invalid decision_type: {req.decision_type}"])
    if req.confidence and req.confidence not in {"high", "medium", "low"}:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=[f"invalid confidence: {req.confidence}"])
    if req.impact_scope and req.impact_scope not in {"narrow", "moderate", "broad"}:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=[f"invalid impact_scope: {req.impact_scope}"])

    # Map string enums to typed enums
    dt = DecisionType(req.decision_type)
    conf = Confidence(req.confidence) if req.confidence else None
    scope = ImpactScope(req.impact_scope) if req.impact_scope else None
    metrics = None
    if req.metrics:
        metrics = PerformanceMetrics(**req.metrics.model_dump())
    alternatives = [Alternative(**a.model_dump()) for a in req.alternatives] if req.alternatives else None

    await _history.record(
        decision=req.decision,
        rationale=req.rationale,
        cycle=req.cycle,
        phase=req.phase,
        agents=req.agents or None,
        room_id=req.room_id,
        sprint=req.sprint,
        sprint_goal=req.sprint_goal,
        metrics=metrics,
        decision_type=dt,
        alternatives=alternatives,
        impact=req.impact,
        confidence=conf,
        impact_scope=scope,
    )

    # Broadcast event
    EventBus.get().publish_nowait(Event(
        type="decision_recorded",
        data={"decision": req.decision, "decision_type": req.decision_type},
    ))

    return {"ok": True, "decision": req.decision}


# ── OKR ──────────────────────────────────────────────────────

@app.get("/okr")
async def get_okr():
    """Extract OKR hierarchy from current consensus."""
    content = await _consensus.read()
    objectives = ConsensusMemory.extract_okrs(content)
    return {"objectives": okr_to_dicts(objectives)}


@app.post("/okr")
async def update_okr(req: OKRRequest):
    """Validate and write OKR data into the consensus ## OKR section."""
    import re

    okr_dicts = [obj.model_dump() for obj in req.objectives]
    issues = validate_okr(okr_dicts)
    if issues:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=issues)

    # Build Markdown OKR section
    lines = ["## OKR"]
    for obj in req.objectives:
        lines.append(f"### {obj.id}: {obj.description}")
        for kr in obj.key_results:
            lines.append(f"- {kr.id}: {kr.description} [progress: {kr.progress}]")
            for init in kr.initiatives:
                owner_part = f" @{init.owner}" if init.owner else ""
                lines.append(f"  - {init.id}: {init.description} [status: {init.status}]{owner_part}")
    okr_md = "\n".join(lines) + "\n"

    # Read current consensus and replace or append OKR section
    content = await _consensus.read()
    if re.search(r"##\s*OKR\s*\n", content):
        # Replace existing OKR section (up to next ## or end)
        content = re.sub(
            r"##\s*OKR\s*\n.*?(?=\n##(?!#)|\Z)",
            okr_md,
            content,
            flags=re.DOTALL,
        )
    else:
        # Append OKR section
        content = content.rstrip() + "\n\n" + okr_md

    await _consensus.write(content)

    # Broadcast event
    EventBus.get().publish_nowait(Event(
        type="okr_updated",
        data={"objectives": okr_dicts},
    ))

    return {"ok": True, "objectives": okr_dicts}


# ── WebSocket ─────────────────────────────────────────────────

_CAMERA_MSG_TYPES = {"landmarks", "gesture", "camera_status"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await _ws_manager.connect(ws)
    try:
        # Send initial state
        rooms = await _director.get_rooms_status()
        await ws.send_json({"type": "init", "rooms_status": rooms})

        # Read incoming messages (pings + camera data)
        while True:
            raw = await ws.receive_text()
            try:
                msg = __import__("json").loads(raw)
                msg_type = msg.get("type", "")
                if msg_type in _CAMERA_MSG_TYPES:
                    from olympus.events.bus import EventBus
                    from olympus.events.types import Event
                    await EventBus.get().publish(Event(
                        type=msg_type,
                        data=msg.get("data", {}),
                    ))
            except (ValueError, TypeError):
                pass  # ignore malformed / plain ping messages
    except WebSocketDisconnect:
        _ws_manager.disconnect(ws)
