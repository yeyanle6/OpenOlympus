# OpenOlympus - CLAUDE.md

## Project: OpenOlympus — AI Multi-Agent Collaboration Framework

## Quick Start

```bash
# Backend
pip install -e ".[api,dev]"
uvicorn olympus.api.app:app --reload --port 8000

# Frontend
cd web && npm install && npm run dev

# Tests
pytest tests/ -v
```

## Architecture

Two operating modes on a shared codebase:

- **Interactive (Web UI)**: User → Director (LLM intent parser) → Room (lifecycle) → Protocol (strategy) → Agents (Claude CLI)
- **Autonomous Loop**: Timer → Engine → Convergence Rules → Claude CLI → Consensus Validation → Backup/Restore

## Directory Map

| Path | Purpose |
|------|---------|
| `agents/` | 12 agent definitions (YAML frontmatter + Markdown) |
| `src/olympus/agent/` | Agent loading, LLM invocation, permission enforcement |
| `src/olympus/protocol/` | 5 collaboration protocols (Strategy pattern) |
| `src/olympus/room/` | Room lifecycle + PauseGate |
| `src/olympus/director/` | LLM-powered intent router |
| `src/olympus/loop/` | Autonomous cycle engine + convergence + stagnation |
| `src/olympus/memory/` | Consensus (with history), decision log, session memory |
| `src/olympus/events/` | EventBus → WebSocket broadcast |
| `src/olympus/api/` | FastAPI REST + WebSocket |
| `web/` | React 19 + Vite + Tailwind frontend |

## Key Design Decisions

- **Stateless cycles**: Each loop cycle is a fresh LLM invocation. All continuity in `consensus.md`.
- **Consensus never overwrites**: Every write archives the previous version to `memories/history/`.
- **Permission isolation**: Planning agents (planner, auditor, critic) cannot write code. Specialist agents are read-only.
- **Convergence rules**: Brainstorm → Evaluate → Execute, with retrospection every 5 cycles.
- **Stagnation detection**: Same Next Action for 2 cycles triggers forced pivot.

## Agents (12 roles, 4 layers)

| Layer | Agents | Can Write | Can Execute |
|-------|--------|-----------|-------------|
| Orchestration | coordinator, tracker | Yes | No |
| Planning | planner, auditor, critic | No | No |
| Worker | builder, worker | Yes | Yes |
| Specialist | architect, researcher, explorer, reviewer, tester | No | No |

## Protocols

| Protocol | Use Case |
|----------|----------|
| `delegate` | Single agent handles task |
| `roundtable` | Multi-agent discussion, converge on [DONE] |
| `peer_review` | Author + reviewer iterate, [APPROVED] to finish |
| `pipeline` | Sequential handoff A → B → C |
| `parallel` | Fan-out/fan-in for concurrent research |
