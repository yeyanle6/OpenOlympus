"""Stagnation and Kanban blocking detection for autonomous loop.

Extends basic stagnation detection with Kanban-style WIP limit tracking
and blocker classification.  The detector now distinguishes between:
 - *stagnation* (same action repeated — existing behaviour)
 - *WIP overflow* (too many in-progress items in consensus)
 - *blocked items* (items explicitly tagged [BLOCKED] in consensus)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class BlockerType(str, Enum):
    STAGNATION = "stagnation"
    WIP_OVERFLOW = "wip_overflow"
    BLOCKED_ITEM = "blocked_item"


@dataclass
class BlockerInfo:
    """A single detected blocker."""

    type: BlockerType
    description: str
    severity: str = "warning"  # "warning" | "critical"
    details: list[str] = field(default_factory=list)


class StagnationDetector:
    """Detects stagnation, WIP overflow, and blocked items in the loop.

    Backwards-compatible: all original methods work unchanged.
    New Kanban features are opt-in via *wip_limit* and *detect_blockers()*.
    """

    def __init__(self, threshold: int = 2, wip_limit: int = 0):
        self.threshold = threshold
        self.wip_limit = wip_limit  # 0 = disabled
        self._recent_actions: list[str] = []

    # ------------------------------------------------------------------
    # Original stagnation API (unchanged)
    # ------------------------------------------------------------------

    def record_action(self, next_action: str) -> None:
        normalized = self._normalize(next_action)
        self._recent_actions.append(normalized)
        if len(self._recent_actions) > 10:
            self._recent_actions = self._recent_actions[-10:]

    def is_stagnant(self) -> bool:
        if len(self._recent_actions) < self.threshold:
            return False
        recent = self._recent_actions[-self.threshold:]
        return len(set(recent)) == 1

    def get_warning(self) -> str:
        if not self.is_stagnant():
            return ""
        action = self._recent_actions[-1]
        return (
            f"WARNING: Stagnation detected! The same Next Action has appeared "
            f"for {self.threshold} consecutive cycles: '{action}'\n"
            f"You MUST change direction, shrink scope, or force-ship something."
        )

    # ------------------------------------------------------------------
    # Kanban blocker detection
    # ------------------------------------------------------------------

    def detect_blockers(self, consensus: str) -> list[BlockerInfo]:
        """Analyse consensus text for all types of blockers."""
        blockers: list[BlockerInfo] = []

        # 1. Stagnation (existing logic)
        if self.is_stagnant():
            blockers.append(BlockerInfo(
                type=BlockerType.STAGNATION,
                description=(
                    f"Same Next Action for {self.threshold} consecutive cycles"
                ),
                severity="critical",
                details=[self._recent_actions[-1]],
            ))

        # 2. WIP overflow
        if self.wip_limit > 0:
            wip_items = self.extract_wip_items(consensus)
            if len(wip_items) > self.wip_limit:
                blockers.append(BlockerInfo(
                    type=BlockerType.WIP_OVERFLOW,
                    description=(
                        f"WIP limit exceeded: {len(wip_items)}/{self.wip_limit} "
                        f"items in progress"
                    ),
                    severity="warning",
                    details=wip_items,
                ))

        # 3. Explicitly blocked items
        blocked = self.extract_blocked_items(consensus)
        for item in blocked:
            blockers.append(BlockerInfo(
                type=BlockerType.BLOCKED_ITEM,
                description=f"Blocked item: {item}",
                severity="critical",
                details=[item],
            ))

        return blockers

    def get_blocker_warning(self, consensus: str) -> str:
        """Return a combined warning string for all detected blockers."""
        blockers = self.detect_blockers(consensus)
        if not blockers:
            return ""
        lines = ["WARNING: Kanban blockers detected!"]
        for b in blockers:
            icon = "🔴" if b.severity == "critical" else "🟡"
            lines.append(f"  {icon} [{b.type.value}] {b.description}")
        lines.append(
            "You MUST resolve blockers before starting new work. "
            "Finish or unblock existing items first."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Consensus extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_next_action(consensus: str) -> str:
        match = re.search(
            r"##\s*Next Action\s*\n(.*?)(?=\n##|\Z)", consensus, re.DOTALL
        )
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def extract_wip_items(consensus: str) -> list[str]:
        """Extract items marked as in-progress from consensus.

        Looks for lines matching:
          - [ ] item          → not in progress (skip)
          - [x] item          → done (skip)
          - [~] item          → in progress
          - [WIP] item        → in progress
          - item [IN PROGRESS] → in progress
        """
        patterns = [
            re.compile(r"^-\s*\[~\]\s*(.+)$", re.MULTILINE),
            re.compile(r"^-\s*\[WIP\]\s*(.+)$", re.MULTILINE | re.IGNORECASE),
            re.compile(r"^-\s*(.+?)\s*\[IN PROGRESS\]\s*$", re.MULTILINE | re.IGNORECASE),
        ]
        items: list[str] = []
        for pat in patterns:
            items.extend(m.group(1).strip() for m in pat.finditer(consensus))
        return items

    @staticmethod
    def extract_blocked_items(consensus: str) -> list[str]:
        """Extract items explicitly tagged [BLOCKED] in consensus."""
        pat = re.compile(
            r"^-\s*(?:\[.\]\s*)?(.+?)\s*\[BLOCKED\](.*)$",
            re.MULTILINE | re.IGNORECASE,
        )
        return [m.group(1).strip() for m in pat.finditer(consensus)]

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text
