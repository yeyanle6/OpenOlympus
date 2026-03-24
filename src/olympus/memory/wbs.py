"""WBS (Work Breakdown Structure) task decomposition model.

Provides a tree-structured task hierarchy for sprint planning.
Each node represents a deliverable that can be broken into subtasks.
Leaf nodes are the actual units of work assigned to agents/cycles.

Example structure::

    WBS Root (Sprint Goal)
    ├── 1. Feature A
    │   ├── 1.1 Design API schema
    │   └── 1.2 Implement endpoints
    └── 2. Feature B
        ├── 2.1 Write tests
        └── 2.2 Documentation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class WBSNode:
    """A single node in the work breakdown structure."""

    id: str  # e.g. "1", "1.1", "1.1.2"
    title: str
    description: str = ""
    parent_id: str = ""  # "" = root-level
    assignee: str = ""  # agent id
    status: TaskStatus = TaskStatus.PENDING
    estimated_cycles: int = 0
    actual_cycles: int = 0
    okr_link: str = ""  # e.g. "O1/KR2" — links to OKR key result
    acceptance_criteria: list[str] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        """Leaf nodes have no children (checked externally by TaskBreakdown)."""
        # Standalone check — the tree manager overrides via children lookup
        return True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "parent_id": self.parent_id,
            "assignee": self.assignee,
            "status": self.status.value,
            "estimated_cycles": self.estimated_cycles,
            "actual_cycles": self.actual_cycles,
            "okr_link": self.okr_link,
            "acceptance_criteria": self.acceptance_criteria,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WBSNode:
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            parent_id=data.get("parent_id", ""),
            assignee=data.get("assignee", ""),
            status=TaskStatus(data.get("status", "pending")),
            estimated_cycles=data.get("estimated_cycles", 0),
            actual_cycles=data.get("actual_cycles", 0),
            okr_link=data.get("okr_link", ""),
            acceptance_criteria=data.get("acceptance_criteria", []),
        )


class TaskBreakdown:
    """Manages a flat collection of WBS nodes with tree semantics.

    Nodes are stored in a dict keyed by ID.  Parent-child relationships
    are expressed via ``parent_id``.  This keeps serialisation trivial
    (list of dicts) while still supporting tree queries.
    """

    def __init__(self, sprint_goal: str = ""):
        self.sprint_goal = sprint_goal
        self._nodes: dict[str, WBSNode] = {}

    def add(self, node: WBSNode) -> None:
        self._nodes[node.id] = node

    def get(self, node_id: str) -> WBSNode | None:
        return self._nodes.get(node_id)

    def remove(self, node_id: str) -> WBSNode | None:
        return self._nodes.pop(node_id, None)

    @property
    def all_nodes(self) -> list[WBSNode]:
        return list(self._nodes.values())

    def roots(self) -> list[WBSNode]:
        """Top-level nodes (no parent)."""
        return [n for n in self._nodes.values() if not n.parent_id]

    def children(self, parent_id: str) -> list[WBSNode]:
        return [n for n in self._nodes.values() if n.parent_id == parent_id]

    def leaves(self) -> list[WBSNode]:
        """Nodes that have no children — the actual work items."""
        parent_ids = {n.parent_id for n in self._nodes.values() if n.parent_id}
        return [n for n in self._nodes.values() if n.id not in parent_ids]

    def by_status(self, status: TaskStatus) -> list[WBSNode]:
        return [n for n in self._nodes.values() if n.status == status]

    def by_assignee(self, assignee: str) -> list[WBSNode]:
        return [n for n in self._nodes.values() if n.assignee == assignee]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def completion_pct(self) -> float:
        """Percentage of leaf nodes that are DONE."""
        leaf_nodes = self.leaves()
        if not leaf_nodes:
            return 0.0
        done = sum(1 for n in leaf_nodes if n.status == TaskStatus.DONE)
        return done / len(leaf_nodes)

    def total_estimated_cycles(self) -> int:
        return sum(n.estimated_cycles for n in self.leaves())

    def total_actual_cycles(self) -> int:
        return sum(n.actual_cycles for n in self.leaves())

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_list(self) -> list[dict]:
        return [n.to_dict() for n in self._nodes.values()]

    @classmethod
    def from_list(cls, data: list[dict], sprint_goal: str = "") -> TaskBreakdown:
        tb = cls(sprint_goal=sprint_goal)
        for item in data:
            tb.add(WBSNode.from_dict(item))
        return tb

    # ------------------------------------------------------------------
    # Markdown rendering (for embedding in consensus)
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the WBS as a Markdown checklist."""
        lines = [f"## Sprint Backlog — {self.sprint_goal}" if self.sprint_goal
                 else "## Sprint Backlog"]

        def _render(nodes: list[WBSNode], indent: int = 0) -> None:
            for node in sorted(nodes, key=lambda n: n.id):
                prefix = "  " * indent
                check = self._status_marker(node.status)
                assignee = f" @{node.assignee}" if node.assignee else ""
                okr = f" (→{node.okr_link})" if node.okr_link else ""
                lines.append(f"{prefix}- [{check}] {node.id}: {node.title}{assignee}{okr}")
                kids = self.children(node.id)
                if kids:
                    _render(kids, indent + 1)

        _render(self.roots())
        return "\n".join(lines)

    @staticmethod
    def _status_marker(status: TaskStatus) -> str:
        return {
            TaskStatus.PENDING: " ",
            TaskStatus.IN_PROGRESS: "~",
            TaskStatus.DONE: "x",
            TaskStatus.BLOCKED: "!",
            TaskStatus.CANCELLED: "-",
        }[status]

    # ------------------------------------------------------------------
    # Parse from Markdown (round-trip support)
    # ------------------------------------------------------------------

    @classmethod
    def from_markdown(cls, text: str) -> TaskBreakdown:
        """Parse a WBS markdown checklist back into a TaskBreakdown.

        Expects lines like::
            - [x] 1.1: Task title @assignee (→O1/KR2)
        """
        goal = ""
        goal_match = re.search(r"## Sprint Backlog\s*—\s*(.+)", text)
        if goal_match:
            goal = goal_match.group(1).strip()

        tb = cls(sprint_goal=goal)
        line_pat = re.compile(
            r"^(\s*)-\s*\[(.)\]\s*(\S+):\s*(.+?)(?:\s+@(\S+))?(?:\s*\(→(\S+)\))?\s*$"
        )
        id_stack: list[tuple[int, str]] = []  # (indent_level, node_id)

        for line in text.split("\n"):
            m = line_pat.match(line)
            if not m:
                continue
            indent = len(m.group(1)) // 2
            status_char = m.group(2)
            node_id = m.group(3)
            title = m.group(4).strip()
            assignee = m.group(5) or ""
            okr_link = m.group(6) or ""

            status_map = {
                " ": TaskStatus.PENDING,
                "~": TaskStatus.IN_PROGRESS,
                "x": TaskStatus.DONE,
                "!": TaskStatus.BLOCKED,
                "-": TaskStatus.CANCELLED,
            }
            status = status_map.get(status_char, TaskStatus.PENDING)

            # Determine parent
            while id_stack and id_stack[-1][0] >= indent:
                id_stack.pop()
            parent_id = id_stack[-1][1] if id_stack else ""

            tb.add(WBSNode(
                id=node_id,
                title=title,
                parent_id=parent_id,
                assignee=assignee,
                status=status,
                okr_link=okr_link,
            ))
            id_stack.append((indent, node_id))

        return tb
