"""Room type aliases — maps natural language meeting names to protocols.

Users can say "开个头脑风暴" instead of "use roundtable protocol".
The Director resolves aliases before protocol selection.
"""

from __future__ import annotations

# Chinese and English aliases → protocol ID
ALIASES: dict[str, str] = {
    # Roundtable
    "头脑风暴": "roundtable",
    "brainstorm": "roundtable",
    "讨论": "roundtable",
    "圆桌": "roundtable",
    "讨论会": "roundtable",
    "discussion": "roundtable",

    # Peer Review
    "评审": "peer_review",
    "评审会": "peer_review",
    "code review": "peer_review",
    "审查": "peer_review",
    "review": "peer_review",

    # Decision Gate
    "决策会": "decision_gate",
    "投票": "decision_gate",
    "决策": "decision_gate",
    "go/no-go": "decision_gate",
    "vote": "decision_gate",

    # Delegate
    "任务": "delegate",
    "执行": "delegate",
    "委派": "delegate",
    "assign": "delegate",

    # Pipeline
    "流水线": "pipeline",
    "流程": "pipeline",
    "chain": "pipeline",

    # Parallel
    "并行调研": "parallel",
    "并行": "parallel",
    "parallel research": "parallel",

    # Standup
    "站会": "standup",
    "晨会": "standup",
    "standup": "standup",
    "daily": "standup",

    # Review Meeting
    "方案评审": "review_meeting",
    "设计评审": "review_meeting",
    "design review": "review_meeting",
}


def resolve_alias(text: str) -> str | None:
    """Try to resolve a protocol alias from text. Returns protocol ID or None."""
    text_lower = text.lower()
    for alias, protocol in ALIASES.items():
        if alias in text_lower:
            return protocol
    return None


def get_aliases_prompt() -> str:
    """Generate prompt section listing available meeting types."""
    # Group by protocol
    by_protocol: dict[str, list[str]] = {}
    for alias, proto in ALIASES.items():
        by_protocol.setdefault(proto, []).append(alias)

    lines = ["Meeting types (aliases for protocols):"]
    for proto, aliases in by_protocol.items():
        lines.append(f"  {proto}: {', '.join(aliases[:4])}")
    return "\n".join(lines)
