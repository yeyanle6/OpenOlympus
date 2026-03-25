"""Reference extraction and management for room discussions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Reference:
    """A single reference cited in a discussion."""

    id: str  # auto-generated
    url: str = ""
    title: str = ""
    ref_type: str = ""  # "url" | "paper" | "dataset" | "tool" | "standard"
    cited_by: list[str] = field(default_factory=list)  # agent_ids
    contexts: list[dict[str, str]] = field(default_factory=list)  # [{agent, topic, quote}]
    message_indices: list[int] = field(default_factory=list)  # which messages cite this
    source_marker_match: bool = False  # True if matched via [SOURCE: ...] inline marker

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "type": self.ref_type,
            "cited_by": self.cited_by,
            "contexts": self.contexts,
            "message_indices": self.message_indices,
            "citation_count": len(self.cited_by),
            "source_marker_match": self.source_marker_match,
        }


# Patterns for extracting references
_URL_PATTERN = re.compile(
    r'https?://[^\s\)\]\}\>,\u3001\u3002\uff0c"\']+', re.UNICODE
)

_PAPER_PATTERNS = [
    # "Author (Year)" or "(Author, Year)" or "Author et al. (Year)"
    re.compile(r'([A-Z][a-z]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][a-z]+)?)\s*[\(（]\s*((?:19|20)\d{2})\s*[\)）]'),
    # "Author, Year" in parentheses
    re.compile(r'[\(（]([A-Z][a-z]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][a-z]+)?),?\s*((?:19|20)\d{2})[\)）]'),
]

# Inline source markers from Specialist agents: [SOURCE: description | type]
_SOURCE_MARKER = re.compile(
    r"\[SOURCE:\s*(.+?)\s*\|\s*(\w+)\s*\]"
)

_DATASET_KEYWORDS = [
    "UBFC-rPPG", "UBFC", "PURE", "COHFACE", "MAHNOB-HCI", "MMSE-HR",
    "OBF", "VIPL-HR", "UCLA-rPPG", "AFRL",
]

_TOOL_KEYWORDS = [
    "MediaPipe", "OpenCV", "TensorFlow", "PyTorch", "ONNX",
    "CoreML", "TFLite", "SciPy", "NumPy", "FFT",
]


class ReferenceExtractor:
    """Extracts and manages references from room discussion messages."""

    def __init__(self) -> None:
        self._refs: dict[str, Reference] = {}  # key -> Reference
        self._counter = 0

    def extract_from_message(
        self, content: str, sender: str, message_index: int
    ) -> list[Reference]:
        """Extract all references from a message and return newly found ones."""
        new_refs: list[Reference] = []

        # Extract URLs
        for url_match in _URL_PATTERN.finditer(content):
            url = url_match.group().rstrip(".")
            ref = self._get_or_create(url, "url", url=url, title=self._guess_url_title(url))
            self._add_citation(ref, sender, message_index, content, url_match.start())
            if ref.id == f"ref-{self._counter}":
                new_refs.append(ref)

        # Extract paper citations
        for pattern in _PAPER_PATTERNS:
            for m in pattern.finditer(content):
                author = m.group(1).strip()
                year = m.group(2)
                key = f"{author}_{year}".lower().replace(" ", "_")
                title = f"{author} ({year})"
                ref = self._get_or_create(key, "paper", title=title)
                self._add_citation(ref, sender, message_index, content, m.start())
                if ref.id == f"ref-{self._counter}":
                    new_refs.append(ref)

        # Extract known datasets
        for ds in _DATASET_KEYWORDS:
            if ds.lower() in content.lower():
                ref = self._get_or_create(ds.lower(), "dataset", title=ds)
                self._add_citation(ref, sender, message_index, content,
                                   content.lower().find(ds.lower()))

        # Extract known tools
        for tool in _TOOL_KEYWORDS:
            if tool.lower() in content.lower():
                ref = self._get_or_create(f"tool_{tool.lower()}", "tool", title=tool)
                self._add_citation(ref, sender, message_index, content,
                                   content.lower().find(tool.lower()))

        # Extract inline [SOURCE: description | type] markers from Specialist agents
        for m in _SOURCE_MARKER.finditer(content):
            desc = m.group(1).strip()
            src_type = m.group(2).strip().lower()
            key = f"source_{desc.lower().replace(' ', '_')[:40]}"
            ref = self._get_or_create(key, src_type, title=desc)
            ref.source_marker_match = True
            self._add_citation(ref, sender, message_index, content, m.start())

        return new_refs

    def get_all(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._refs.values()]

    def get_by_agent(self, agent_id: str) -> list[dict[str, Any]]:
        return [
            r.to_dict() for r in self._refs.values()
            if agent_id in r.cited_by
        ]

    def get_graph_data(self) -> dict[str, Any]:
        """Return data structured for tree/circle visualization."""
        # Group by type
        by_type: dict[str, list[dict]] = {}
        for ref in self._refs.values():
            by_type.setdefault(ref.ref_type, []).append(ref.to_dict())

        # Build agent -> refs mapping
        agent_refs: dict[str, list[str]] = {}
        for ref in self._refs.values():
            for agent in ref.cited_by:
                agent_refs.setdefault(agent, []).append(ref.id)

        # Build tree structure
        tree = {
            "name": "References",
            "children": []
        }
        for ref_type, refs in by_type.items():
            type_node = {
                "name": ref_type,
                "children": [
                    {
                        "name": r["title"],
                        "id": r["id"],
                        "url": r.get("url", ""),
                        "cited_by": r["cited_by"],
                        "citation_count": r["citation_count"],
                    }
                    for r in refs
                ]
            }
            tree["children"].append(type_node)

        return {
            "tree": tree,
            "by_type": by_type,
            "agent_refs": agent_refs,
            "total": len(self._refs),
            "stats": {
                ref_type: len(refs) for ref_type, refs in by_type.items()
            },
        }

    def _get_or_create(
        self, key: str, ref_type: str, url: str = "", title: str = ""
    ) -> Reference:
        if key not in self._refs:
            self._counter += 1
            self._refs[key] = Reference(
                id=f"ref-{self._counter}",
                url=url,
                title=title or key,
                ref_type=ref_type,
            )
        return self._refs[key]

    def _add_citation(
        self, ref: Reference, sender: str, msg_idx: int, content: str, pos: int
    ) -> None:
        if sender not in ref.cited_by:
            ref.cited_by.append(sender)
        if msg_idx not in ref.message_indices:
            ref.message_indices.append(msg_idx)

        # Extract surrounding context (±100 chars)
        start = max(0, pos - 100)
        end = min(len(content), pos + 100)
        quote = content[start:end].strip()
        ref.contexts.append({
            "agent": sender,
            "quote": quote,
            "message_index": msg_idx,
        })

    @staticmethod
    def _guess_url_title(url: str) -> str:
        """Guess a readable title from URL."""
        # Remove protocol and common prefixes
        clean = re.sub(r'^https?://(www\.)?', '', url)
        # Take domain + first path segment
        parts = clean.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return parts[0]
