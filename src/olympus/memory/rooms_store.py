"""File-backed persistence for room data (messages, metadata, references)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiofiles


class RoomsStore:
    """Persists room messages, metadata, and references to disk."""

    def __init__(self, base_dir: str | Path = "memories/rooms"):
        self.base_dir = Path(base_dir)

    def _room_dir(self, room_id: str) -> Path:
        return self.base_dir / room_id

    def _ensure_dir(self, room_id: str) -> Path:
        d = self._room_dir(room_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Messages ──────────────────────────────────────────────

    async def save_message(self, room_id: str, msg: dict[str, str]) -> None:
        d = self._ensure_dir(room_id)
        path = d / "messages.jsonl"
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    async def load_messages(self, room_id: str) -> list[dict[str, str]]:
        path = self._room_dir(room_id) / "messages.jsonl"
        if not path.exists():
            return []
        messages = []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        return messages

    # ── Room metadata ─────────────────────────────────────────

    async def save_room_meta(self, room_id: str, meta: dict[str, Any]) -> None:
        d = self._ensure_dir(room_id)
        path = d / "meta.json"
        tmp = path.with_suffix(".tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))
        os.replace(str(tmp), str(path))

    async def load_room_meta(self, room_id: str) -> dict[str, Any] | None:
        path = self._room_dir(room_id) / "meta.json"
        if not path.exists():
            return None
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                text = await f.read()
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to salvage first JSON object from corrupted file
            try:
                depth = 0
                for i, c in enumerate(text):
                    if c == '{': depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            return json.loads(text[:i + 1])
            except (json.JSONDecodeError, ValueError):
                pass
            return None

    # ── References ────────────────────────────────────────────

    async def save_references(self, room_id: str, refs_data: dict[str, Any]) -> None:
        d = self._ensure_dir(room_id)
        path = d / "references.json"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(refs_data, ensure_ascii=False, indent=2))

    async def load_references(self, room_id: str) -> dict[str, Any] | None:
        path = self._room_dir(room_id) / "references.json"
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            return json.loads(await f.read())

    # ── List all rooms ────────────────────────────────────────

    async def list_room_ids(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return [
            d.name for d in sorted(self.base_dir.iterdir())
            if d.is_dir() and (d / "meta.json").exists()
        ]

    async def load_all_rooms(self) -> list[dict[str, Any]]:
        """Load all room metadata for restoring state on startup."""
        rooms = []
        for room_id in await self.list_room_ids():
            meta = await self.load_room_meta(room_id)
            if meta:
                rooms.append(meta)
        return rooms
