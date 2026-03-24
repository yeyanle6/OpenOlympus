"""File-backed consensus memory with history accumulation.

Extended with OKR (Objectives & Key Results) field extraction and
validation.  The OKR section is optional in consensus.md and follows
a structured Markdown format:

    ## OKR
    ### O1: <Objective text>
    - KR1: <Key Result> [progress: 0.7]
    - KR2: <Key Result> [progress: 0.3]
    ### O2: ...
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiofiles


@dataclass
class Initiative:
    """An actionable initiative linked to a key result."""

    id: str  # e.g. "I1"
    description: str
    status: str = "pending"  # pending | in_progress | done | blocked
    owner: str = ""  # agent or person responsible


@dataclass
class KeyResult:
    """A single measurable key result."""

    id: str  # e.g. "KR1"
    description: str
    progress: float = 0.0  # 0.0 – 1.0
    initiatives: list[Initiative] = field(default_factory=list)


@dataclass
class Objective:
    """An OKR objective with its key results."""

    id: str  # e.g. "O1"
    description: str
    key_results: list[KeyResult] = field(default_factory=list)

    @property
    def progress(self) -> float:
        """Average progress across all key results."""
        if not self.key_results:
            return 0.0
        return sum(kr.progress for kr in self.key_results) / len(self.key_results)


class ConsensusMemory:
    """Atomic consensus file with automatic archiving on every write."""

    def __init__(
        self,
        path: str | Path = "memories/consensus.md",
        history_dir: str | Path = "memories/history",
    ):
        self.path = Path(path)
        self.history_dir = Path(history_dir)
        self._lock = asyncio.Lock()

    async def read(self) -> str:
        if not self.path.exists():
            return ""
        async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
            return await f.read()

    async def write(self, content: str) -> None:
        """Write new consensus, auto-archive the previous version."""
        async with self._lock:
            if self.path.exists():
                await self._archive_current()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
                await f.write(content)
            await asyncio.to_thread(os.replace, str(tmp), str(self.path))

    async def backup(self) -> Path | None:
        """Pre-cycle backup to .bak file."""
        if not self.path.exists():
            return None
        bak = self.path.with_suffix(".md.bak")
        await asyncio.to_thread(shutil.copy2, str(self.path), str(bak))
        return bak

    async def restore(self) -> bool:
        """Restore consensus from .bak on cycle failure."""
        bak = self.path.with_suffix(".md.bak")
        if not bak.exists():
            return False
        await asyncio.to_thread(shutil.copy2, str(bak), str(self.path))
        return True

    async def has_changed_since_backup(self) -> bool:
        """Check if consensus was modified since last backup."""
        bak = self.path.with_suffix(".md.bak")
        if not bak.exists() or not self.path.exists():
            return True
        return await asyncio.to_thread(self._files_differ, self.path, bak)

    async def get_history(self, limit: int = 10) -> list[tuple[str, str]]:
        """Return [(timestamp, content), ...] for recent history."""
        if not self.history_dir.exists():
            return []
        files = sorted(self.history_dir.glob("*.md"), reverse=True)[:limit]
        results = []
        for f in files:
            ts = f.stem
            async with aiofiles.open(f, "r", encoding="utf-8") as fh:
                content = await fh.read()
            results.append((ts, content))
        return results

    # ------------------------------------------------------------------
    # OKR extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_okrs(consensus: str) -> list[Objective]:
        """Parse ``## OKR`` section from consensus Markdown into typed objects."""
        # Isolate the ## OKR block
        okr_match = re.search(
            r"##\s*OKR\s*\n(.*?)(?=\n##(?!#)|\Z)", consensus, re.DOTALL
        )
        if not okr_match:
            return []

        okr_text = okr_match.group(1)
        objectives: list[Objective] = []

        # Split on ### headings (objectives)
        obj_pattern = re.compile(
            r"###\s*(O\d+)\s*:\s*(.+?)(?=\n###|\Z)", re.DOTALL
        )
        for obj_m in obj_pattern.finditer(okr_text):
            obj_id = obj_m.group(1).strip()
            lines = obj_m.group(2).strip().split("\n")
            obj_desc = lines[0].strip()

            key_results: list[KeyResult] = []
            kr_pat = re.compile(
                r"^-\s*(KR\d+)\s*:\s*(.+?)(?:\[progress:\s*([\d.]+)\])?\s*$"
            )
            init_pat = re.compile(
                r"^-\s*(I\d+)\s*:\s*(.+?)(?:\[status:\s*(\w+)\])?(?:\s*@(\S+))?\s*$"
            )
            current_kr: KeyResult | None = None
            for line in lines[1:]:
                stripped = line.strip()
                kr_m = kr_pat.match(stripped)
                if kr_m:
                    progress = float(kr_m.group(3)) if kr_m.group(3) else 0.0
                    current_kr = KeyResult(
                        id=kr_m.group(1),
                        description=kr_m.group(2).strip(),
                        progress=min(1.0, max(0.0, progress)),
                    )
                    key_results.append(current_kr)
                    continue
                # Sub-bullet initiatives (indented under a KR)
                if current_kr is not None and line.startswith("  "):
                    init_m = init_pat.match(stripped)
                    if init_m:
                        current_kr.initiatives.append(Initiative(
                            id=init_m.group(1),
                            description=init_m.group(2).strip(),
                            status=init_m.group(3) or "pending",
                            owner=init_m.group(4) or "",
                        ))

            objectives.append(Objective(
                id=obj_id,
                description=obj_desc,
                key_results=key_results,
            ))

        return objectives

    @staticmethod
    def validate_okr_section(consensus: str) -> list[str]:
        """Return a list of validation issues (empty = valid)."""
        issues: list[str] = []
        objectives = ConsensusMemory.extract_okrs(consensus)
        if not objectives:
            return issues  # OKR section is optional

        seen_ids: set[str] = set()
        for obj in objectives:
            if obj.id in seen_ids:
                issues.append(f"Duplicate objective ID: {obj.id}")
            seen_ids.add(obj.id)

            if not obj.key_results:
                issues.append(f"{obj.id} has no key results")

            kr_ids: set[str] = set()
            for kr in obj.key_results:
                if kr.id in kr_ids:
                    issues.append(f"{obj.id}/{kr.id} is duplicated")
                kr_ids.add(kr.id)
                if not (0.0 <= kr.progress <= 1.0):
                    issues.append(
                        f"{obj.id}/{kr.id} progress out of range: {kr.progress}"
                    )
                # Validate initiatives
                init_ids: set[str] = set()
                for init in kr.initiatives:
                    if init.id in init_ids:
                        issues.append(
                            f"{obj.id}/{kr.id}/{init.id} is duplicated"
                        )
                    init_ids.add(init.id)
                    valid_statuses = {"pending", "in_progress", "done", "blocked"}
                    if init.status not in valid_statuses:
                        issues.append(
                            f"{obj.id}/{kr.id}/{init.id} invalid status: {init.status}"
                        )

        return issues

    # ------------------------------------------------------------------
    # OKR serialization (objects → Markdown)
    # ------------------------------------------------------------------

    @staticmethod
    def serialize_okrs(objectives: list[Objective]) -> str:
        """Render OKR objects back to the ``## OKR`` Markdown format."""
        if not objectives:
            return ""
        lines = ["## OKR"]
        for obj in objectives:
            lines.append(f"### {obj.id}: {obj.description}")
            for kr in obj.key_results:
                lines.append(
                    f"- {kr.id}: {kr.description} [progress: {kr.progress:.2f}]"
                )
                for init in kr.initiatives:
                    parts = [f"  - {init.id}: {init.description}"]
                    parts.append(f"[status: {init.status}]")
                    if init.owner:
                        parts.append(f"@{init.owner}")
                    lines.append(" ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def update_okr_section(consensus: str, objectives: list[Objective]) -> str:
        """Replace the ``## OKR`` section in consensus with serialized objectives.

        If no OKR section exists, appends one before ``## Next Action`` (or at end).
        """
        new_okr = ConsensusMemory.serialize_okrs(objectives)
        if not new_okr:
            return consensus

        # Try to replace existing ## OKR section
        okr_pattern = re.compile(
            r"##\s*OKR\s*\n.*?(?=\n##(?!#)|\Z)", re.DOTALL
        )
        if okr_pattern.search(consensus):
            return okr_pattern.sub(new_okr, consensus)

        # No existing section — insert before ## Next Action or append
        next_action_match = re.search(r"\n(## Next Action)", consensus)
        if next_action_match:
            pos = next_action_match.start()
            return consensus[:pos] + "\n\n" + new_okr + "\n" + consensus[pos:]
        return consensus.rstrip() + "\n\n" + new_okr + "\n"

    # ------------------------------------------------------------------
    # OKR progress computation
    # ------------------------------------------------------------------

    @staticmethod
    def update_kr_progress_from_initiatives(
        objectives: list[Objective],
    ) -> list[Objective]:
        """Recompute each KR's progress as the fraction of its done initiatives.

        KRs with no initiatives keep their existing progress value.
        Returns the same objects (mutated in place) for convenience.
        """
        for obj in objectives:
            for kr in obj.key_results:
                if not kr.initiatives:
                    continue
                done = sum(1 for i in kr.initiatives if i.status == "done")
                kr.progress = done / len(kr.initiatives)
        return objectives

    @staticmethod
    def transition_initiative_statuses(
        old_objectives: list[Objective],
        new_objectives: list[Objective],
    ) -> list[tuple[str, str, str, str]]:
        """Detect and apply initiative status transitions.

        Compares old vs new consensus OKR sections.  For each initiative
        present in *new_objectives*:
        - If it was ``pending`` before and now appears in a WIP/active context
          in the new consensus, transition to ``in_progress``.
        - If all acceptance signals are met (status already ``done``), keep it.

        Transitions applied in-place on *new_objectives*.
        Returns a list of ``(obj_id, kr_id, init_id, old_status → new_status)`` tuples.
        """
        # Build lookup: init_id → old status
        old_status: dict[str, str] = {}
        for obj in old_objectives:
            for kr in obj.key_results:
                for init in kr.initiatives:
                    old_status[init.id] = init.status

        transitions: list[tuple[str, str, str, str]] = []
        for obj in new_objectives:
            for kr in obj.key_results:
                for init in kr.initiatives:
                    prev = old_status.get(init.id, "pending")
                    # Auto-transition: pending → in_progress when status changed
                    if prev == "pending" and init.status == "in_progress":
                        transitions.append(
                            (obj.id, kr.id, init.id, f"pending → in_progress")
                        )
                    # Auto-transition: in_progress → done
                    elif prev == "in_progress" and init.status == "done":
                        transitions.append(
                            (obj.id, kr.id, init.id, f"in_progress → done")
                        )
                    # Blocked transitions
                    elif prev != "blocked" and init.status == "blocked":
                        transitions.append(
                            (obj.id, kr.id, init.id, f"{prev} → blocked")
                        )
                    # Any other change
                    elif prev != init.status:
                        transitions.append(
                            (obj.id, kr.id, init.id, f"{prev} → {init.status}")
                        )
        return transitions

    # ------------------------------------------------------------------
    # OKR–Sprint alignment
    # ------------------------------------------------------------------

    @staticmethod
    def validate_sprint_okr_alignment(
        consensus: str, sprint_goal: str
    ) -> list[str]:
        """Check that a sprint goal references at least one OKR objective.

        Returns a list of warnings (empty = aligned).  Alignment is checked
        by looking for objective IDs (O1, O2 …) mentioned in the sprint goal
        that also exist in the ## OKR section.
        """
        issues: list[str] = []
        if not sprint_goal:
            issues.append("Sprint goal is empty — cannot assess OKR alignment")
            return issues

        objectives = ConsensusMemory.extract_okrs(consensus)
        if not objectives:
            # No OKR section — alignment is vacuously OK
            return issues

        obj_ids = {obj.id for obj in objectives}
        # Look for references like "O1", "O2" in the sprint goal
        referenced = set(re.findall(r"\bO\d+\b", sprint_goal))
        if not referenced:
            issues.append(
                f"Sprint goal does not reference any OKR objective "
                f"(available: {', '.join(sorted(obj_ids))})"
            )
            return issues

        unknown = referenced - obj_ids
        if unknown:
            issues.append(
                f"Sprint goal references unknown objectives: "
                f"{', '.join(sorted(unknown))}"
            )

        return issues

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _archive_current(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dest = self.history_dir / f"{ts}.md"
        await asyncio.to_thread(shutil.copy2, str(self.path), str(dest))

    @staticmethod
    def _files_differ(a: Path, b: Path) -> bool:
        return a.read_bytes() != b.read_bytes()
