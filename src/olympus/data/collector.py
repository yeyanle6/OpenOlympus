"""Ingestion workers that feed data into the SQLite pipeline.

CycleMetricsCollector — subscribes to EventBus "cycle_complete" events
GitCollector          — polls local git log on a timer
OkrCollector          — snapshots OKR state from ConsensusMemory each cycle
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone

from olympus.data.database import Database
from olympus.data.models import CycleMetric, GitCommit, OkrSnapshot
from olympus.events.bus import EventBus
from olympus.events.types import Event

logger = logging.getLogger(__name__)


class CycleMetricsCollector:
    """Subscribes to EventBus and records cycle_metrics rows."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._bus = EventBus.get()

    async def start(self) -> None:
        self._bus.subscribe(self._handle_event)
        logger.info("CycleMetricsCollector started")

    async def stop(self) -> None:
        self._bus.unsubscribe(self._handle_event)

    async def _handle_event(self, event: Event) -> None:
        if event.type != "cycle_complete":
            return
        d = event.data
        metric = CycleMetric(
            cycle=d.get("cycle", 0),
            phase=d.get("phase", ""),
            duration_ms=d.get("duration_ms", 0),
            cost_usd=d.get("cost", 0.0),
            tokens_used=d.get("tokens_used", 0),
            blockers_detected=d.get("blockers", 0),
            blocker_types=",".join(d.get("blocker_types", [])),
        )
        try:
            await self._db.insert_cycle_metric(**asdict(metric))
        except Exception:
            logger.exception("Failed to insert cycle metric for cycle %d", metric.cycle)


class GitCollector:
    """Polls local git log and upserts new commits into SQLite."""

    def __init__(
        self,
        db: Database,
        repo_path: str = ".",
        poll_interval: int = 900,  # 15 min default
    ) -> None:
        self._db = db
        self._repo_path = repo_path
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("GitCollector started (interval=%ds)", self._poll_interval)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.collect()
            except Exception:
                logger.exception("GitCollector poll failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass

    async def collect(self) -> int:
        """Run git log and upsert commits. Returns count of new commits."""
        commits = await asyncio.to_thread(self._parse_git_log)
        count = 0
        for c in commits:
            try:
                await self._db.upsert_git_commit(**asdict(c))
                count += 1
            except Exception:
                logger.exception("Failed to upsert commit %s", c.sha)
        return count

    def _parse_git_log(self, max_count: int = 100) -> list[GitCommit]:
        """Parse recent git log into GitCommit objects."""
        # Format: sha|author|timestamp|message
        fmt = "%H|%an|%aI|%s"
        try:
            result = subprocess.run(
                [
                    "git", "log",
                    f"--max-count={max_count}",
                    f"--format={fmt}",
                    "--shortstat",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self._repo_path,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        if result.returncode != 0:
            return []

        commits: list[GitCommit] = []
        lines = result.stdout.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or "|" not in line:
                i += 1
                continue

            parts = line.split("|", 3)
            if len(parts) < 4:
                i += 1
                continue

            sha, author, ts, message = parts
            files_changed = insertions = deletions = 0

            # Next non-empty line might be --shortstat output
            if i + 1 < len(lines):
                stat_line = lines[i + 1].strip()
                if stat_line and "changed" in stat_line:
                    files_changed, insertions, deletions = self._parse_shortstat(
                        stat_line
                    )
                    i += 1

            commits.append(
                GitCommit(
                    sha=sha.strip(),
                    author=author.strip(),
                    message=message.strip(),
                    timestamp=ts.strip(),
                    files_changed=files_changed,
                    insertions=insertions,
                    deletions=deletions,
                )
            )
            i += 1

        return commits

    @staticmethod
    def _parse_shortstat(line: str) -> tuple[int, int, int]:
        """Parse '3 files changed, 10 insertions(+), 2 deletions(-)'."""
        files = ins = dels = 0
        for part in line.split(","):
            part = part.strip()
            if "file" in part:
                files = int(part.split()[0])
            elif "insertion" in part:
                ins = int(part.split()[0])
            elif "deletion" in part:
                dels = int(part.split()[0])
        return files, ins, dels


class OkrCollector:
    """Snapshots OKR progress from consensus into SQLite on each cycle."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._bus = EventBus.get()

    async def start(self) -> None:
        self._bus.subscribe(self._handle_event)
        logger.info("OkrCollector started")

    async def stop(self) -> None:
        self._bus.unsubscribe(self._handle_event)

    async def _handle_event(self, event: Event) -> None:
        if event.type != "consensus_updated":
            return
        content = event.data.get("content", "")
        cycle = event.data.get("cycle", 0)
        if not content:
            return
        await self._snapshot_okrs(content, cycle)

    async def _snapshot_okrs(self, consensus: str, cycle: int) -> None:
        from olympus.memory.consensus import ConsensusMemory

        okrs = ConsensusMemory.extract_okrs(consensus)
        for obj in okrs:
            kr_data = [
                {"id": kr.id, "description": kr.description, "progress": kr.progress}
                for kr in obj.key_results
            ]
            snap = OkrSnapshot(
                objective_id=obj.id,
                objective_desc=obj.description,
                progress=obj.progress,
                key_results_json=json.dumps(kr_data, ensure_ascii=False),
                cycle=cycle,
            )
            try:
                await self._db.insert_okr_snapshot(**{
                    "objective_id": snap.objective_id,
                    "objective_desc": snap.objective_desc,
                    "progress": snap.progress,
                    "key_results_json": snap.key_results_json,
                    "cycle": snap.cycle,
                    "timestamp": snap.timestamp,
                })
            except Exception:
                logger.exception("Failed to insert OKR snapshot for %s", obj.id)
