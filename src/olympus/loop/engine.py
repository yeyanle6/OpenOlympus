"""Autonomous loop engine — Python port of auto-loop.sh logic.

Extended with Sprint boundaries, Kanban WIP limits, OKR tracking,
and per-cycle performance metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from olympus.loop.convergence import ConvergenceController, Phase, SprintConfig
from olympus.loop.stagnation import BlockerType, StagnationDetector
from olympus.memory.consensus import ConsensusMemory, Objective
from olympus.memory.history import (
    Confidence,
    DecisionHistory,
    ImpactScope,
    PerformanceMetrics,
)
from olympus.events.bus import EventBus
from olympus.events.types import Event

logger = logging.getLogger(__name__)


@dataclass
class LoopConfig:
    loop_interval: int = 30
    cycle_timeout: int = 1800
    max_consecutive_errors: int = 5
    cooldown_seconds: int = 300
    retrospect_interval: int = 5
    prompt_path: str = "PROMPT.md"
    engine: str = "claude"
    # Project management extensions
    sprint: SprintConfig | None = None
    wip_limit: int = 0  # 0 = disabled


@dataclass
class LoopState:
    cycle_count: int = 0
    error_count: int = 0
    status: str = "idle"  # idle | running | stopped | cooldown
    last_cycle_time: str = ""
    last_cost: float = 0.0
    total_cost: float = 0.0


class LoopEngine:
    """Main autonomous loop engine."""

    def __init__(
        self,
        config: LoopConfig | None = None,
        consensus: ConsensusMemory | None = None,
        history: DecisionHistory | None = None,
    ):
        self.config = config or LoopConfig()
        self.consensus = consensus or ConsensusMemory()
        self.history = history or DecisionHistory()
        self.convergence = ConvergenceController(
            self.config.retrospect_interval,
            sprint=self.config.sprint,
        )
        self.stagnation = StagnationDetector(wip_limit=self.config.wip_limit)
        self.state = LoopState()
        self._stop_event = asyncio.Event()
        self._bus = EventBus.get()

    async def start(self) -> None:
        """Start the autonomous loop."""
        if self.state.status == "running":
            return
        self._stop_event.clear()
        self.state.status = "running"
        logger.info("Loop engine started")

        try:
            while not self._stop_event.is_set():
                await self._run_cycle()

                # Circuit breaker
                if self.state.error_count >= self.config.max_consecutive_errors:
                    logger.warning(
                        "Circuit breaker: %d consecutive errors, cooling down %ds",
                        self.state.error_count,
                        self.config.cooldown_seconds,
                    )
                    self.state.status = "cooldown"
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.config.cooldown_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
                    self.state.error_count = 0
                    self.state.status = "running"
                    continue

                # Wait for next cycle or stop
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.loop_interval,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self.state.status = "stopped"
            logger.info("Loop engine stopped")

    def stop(self) -> None:
        self._stop_event.set()

    async def _run_cycle(self) -> None:
        self.state.cycle_count += 1
        cycle = self.state.cycle_count
        t0 = time.monotonic()
        logger.info("Starting cycle %d", cycle)

        # 1. Backup consensus
        await self.consensus.backup()

        # 2. Read current consensus
        consensus_text = await self.consensus.read()

        # 3. Determine phase
        phase = self.convergence.get_phase(cycle)
        phase_rules = self.convergence.get_phase_rules(phase)

        # 4. Check stagnation + Kanban blockers
        stagnation_warning = self.stagnation.get_warning()
        blocker_warning = self.stagnation.get_blocker_warning(consensus_text)
        combined_warning = "\n\n".join(
            w for w in [stagnation_warning, blocker_warning] if w
        )

        # 4b. Detect blockers for metrics
        blockers = self.stagnation.detect_blockers(consensus_text)

        # 5. Build prompt
        prompt = self._build_prompt(consensus_text, phase_rules, combined_warning)

        # 5b. Inject OKR context if available
        okrs = ConsensusMemory.extract_okrs(consensus_text)
        if okrs:
            okr_lines = ["# OKR Alignment"]
            for obj in okrs:
                okr_lines.append(
                    f"- {obj.id}: {obj.description} "
                    f"(progress: {obj.progress:.0%})"
                )
                for kr in obj.key_results:
                    okr_lines.append(
                        f"  - {kr.id}: {kr.description} [{kr.progress:.0%}]"
                    )
            prompt += "\n\n" + "\n".join(okr_lines)

        # 6. Read PROMPT.md for system instructions
        prompt_path = Path(self.config.prompt_path)
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")
            prompt = system_prompt + "\n\n" + prompt

        # 7. Execute
        sprint_num = self.convergence.current_sprint(cycle) or None
        try:
            result = await asyncio.to_thread(
                self._call_engine, prompt, self.config.cycle_timeout
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            # 8. Validate consensus
            new_consensus = await self.consensus.read()
            if self._validate_consensus(new_consensus):
                self.state.error_count = 0
                self.state.last_cost = result.get("cost_usd", 0.0)
                self.state.total_cost += self.state.last_cost

                # Track stagnation
                next_action = StagnationDetector.extract_next_action(new_consensus)
                self.stagnation.record_action(next_action)

                # 9. OKR progress tracking
                new_okrs = ConsensusMemory.extract_okrs(new_consensus)
                transitions: list[tuple[str, str, str, str]] = []
                if new_okrs:
                    # Detect initiative status transitions
                    transitions = ConsensusMemory.transition_initiative_statuses(
                        okrs, new_okrs
                    )
                    if transitions:
                        logger.info(
                            "Cycle %d: %d initiative transitions: %s",
                            cycle,
                            len(transitions),
                            ", ".join(t[3] for t in transitions),
                        )

                    # Recompute KR progress from initiative completion
                    ConsensusMemory.update_kr_progress_from_initiatives(new_okrs)

                    # Write updated OKRs back to consensus
                    updated_consensus = ConsensusMemory.update_okr_section(
                        new_consensus, new_okrs
                    )
                    if updated_consensus != new_consensus:
                        await self.consensus.write(updated_consensus)

                # 10. Derive confidence and impact scope
                confidence, impact_scope = self._derive_confidence_and_impact(
                    phase, blockers, transitions, new_okrs
                )

                # Build performance metrics
                metrics = PerformanceMetrics(
                    cycle_duration_ms=elapsed_ms,
                    cost_usd=self.state.last_cost,
                    tokens_used=result.get("tokens_used", 0),
                    blockers_detected=len(blockers),
                    blocker_types=[b.type.value for b in blockers],
                )

                # Record decision with metrics + confidence/impact
                await self.history.record(
                    decision=f"Cycle {cycle} ({phase.value}) completed",
                    rationale=next_action,
                    cycle=cycle,
                    phase=phase.value,
                    sprint=sprint_num,
                    metrics=metrics,
                    confidence=confidence,
                    impact_scope=impact_scope,
                )

                # Publish event
                self._bus.publish_nowait(Event(
                    type="cycle_complete",
                    data={
                        "cycle": cycle,
                        "phase": phase.value,
                        "cost": self.state.last_cost,
                        "duration_ms": elapsed_ms,
                        "tokens_used": result.get("tokens_used", 0),
                        "sprint": sprint_num,
                        "blockers": len(blockers),
                        "blocker_types": [b.type.value for b in blockers],
                        "okr_transitions": len(transitions),
                        "confidence": confidence.value,
                        "impact_scope": impact_scope.value,
                    },
                ))

                logger.info("Cycle %d completed (phase: %s)", cycle, phase.value)
            else:
                logger.warning("Cycle %d: consensus validation failed, restoring", cycle)
                await self.consensus.restore()
                self.state.error_count += 1

        except subprocess.TimeoutExpired:
            logger.warning("Cycle %d timed out", cycle)
            # Check if consensus was updated despite timeout
            if await self.consensus.has_changed_since_backup():
                new_consensus = await self.consensus.read()
                if self._validate_consensus(new_consensus):
                    logger.info("Cycle %d timed out but consensus was updated, counting as success", cycle)
                    self.state.error_count = 0
                    return
            await self.consensus.restore()
            self.state.error_count += 1

        except Exception as e:
            logger.error("Cycle %d failed: %s", cycle, e)
            await self.consensus.restore()
            self.state.error_count += 1

    def _build_prompt(
        self, consensus: str, phase_rules: str, stagnation_warning: str
    ) -> str:
        parts = [
            f"# Cycle {self.state.cycle_count}",
            phase_rules,
        ]
        if stagnation_warning:
            parts.append(stagnation_warning)
        if consensus:
            parts.append(f"# Current Consensus\n\n{consensus}")
        else:
            parts.append(
                "# No consensus yet\n\n"
                "This is the first cycle. Create a consensus.md with the required sections."
            )
        return "\n\n".join(parts)

    def _call_engine(self, prompt: str, timeout: int) -> dict:
        cmd = [
            self.config.engine,
            "-p", prompt,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Engine exited {proc.returncode}: {proc.stderr[:500]}")
        return json.loads(proc.stdout)

    @staticmethod
    def _derive_confidence_and_impact(
        phase: Phase,
        blockers: list,
        transitions: list[tuple[str, str, str, str]],
        okrs: list[Objective],
    ) -> tuple[Confidence, ImpactScope]:
        """Derive confidence and impact scope from cycle context.

        Heuristics:
        - Confidence degrades with blockers and early phases.
        - Impact scope widens with more OKR transitions and execution phases.
        """
        # --- Confidence ---
        if len(blockers) >= 2 or phase in (Phase.BRAINSTORM,):
            confidence = Confidence.LOW
        elif len(blockers) >= 1 or phase == Phase.EVALUATE:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.HIGH

        # Stagnation always degrades confidence
        if any(getattr(b, "type", None) == BlockerType.STAGNATION for b in blockers):
            confidence = Confidence.LOW

        # --- Impact scope ---
        if phase in (Phase.SPRINT_REVIEW, Phase.RETROSPECT):
            impact_scope = ImpactScope.BROAD
        elif not transitions and not okrs:
            impact_scope = ImpactScope.NARROW
        elif len(transitions) >= 3:
            impact_scope = ImpactScope.BROAD
        elif transitions or phase == Phase.EXECUTE:
            impact_scope = ImpactScope.MODERATE
        else:
            impact_scope = ImpactScope.NARROW

        return confidence, impact_scope

    @staticmethod
    def _validate_consensus(content: str) -> bool:
        if not content.strip():
            return False
        required = ["## Next Action", "## Company State"]
        return all(header in content for header in required)
