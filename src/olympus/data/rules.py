"""Rule engine — evaluates threshold-based alert rules on EventBus events.

Subscribes to "cycle_complete" events. For each rule, checks the metric
against the threshold. Fires at most once per cooldown_cycles window.
Alerts are persisted to SQLite and published on the EventBus.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from olympus.data.database import Database
from olympus.data.models import Alert, AlertSeverity, Operator, Rule
from olympus.events.bus import EventBus
from olympus.events.types import Event

logger = logging.getLogger(__name__)

# Maps metric names to event.data keys
_METRIC_MAP = {
    "cost_usd": "cost",
    "duration_ms": "duration_ms",
    "tokens_used": "tokens_used",
    "blockers_detected": "blockers",
}


def _compare(value: float, op: Operator, threshold: float) -> bool:
    match op:
        case Operator.GT:
            return value > threshold
        case Operator.GTE:
            return value >= threshold
        case Operator.LT:
            return value < threshold
        case Operator.LTE:
            return value <= threshold
        case Operator.EQ:
            return value == threshold
        case Operator.NEQ:
            return value != threshold


class RuleEngine:
    """Evaluates rules against cycle metrics. Publishes alerts via EventBus."""

    def __init__(self, db: Database, rules: list[Rule] | None = None) -> None:
        self._db = db
        self._bus = EventBus.get()
        self._rules: list[Rule] = rules or self._default_rules()
        # Track last-fired cycle per rule for deduplication
        self._last_fired: dict[str, int] = {}

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def add_rule(self, rule: Rule) -> None:
        self._rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    async def start(self) -> None:
        self._bus.subscribe(self._handle_event)
        logger.info("RuleEngine started with %d rules", len(self._rules))

    async def stop(self) -> None:
        self._bus.unsubscribe(self._handle_event)

    async def _handle_event(self, event: Event) -> None:
        if event.type != "cycle_complete":
            return
        await self.evaluate(event.data)

    async def evaluate(self, data: dict[str, Any]) -> list[Alert]:
        """Evaluate all rules against the given cycle data. Returns fired alerts."""
        cycle = data.get("cycle", 0)
        fired: list[Alert] = []

        for rule in self._rules:
            if not rule.enabled:
                continue

            # Get metric value from event data
            data_key = _METRIC_MAP.get(rule.metric, rule.metric)
            value = data.get(data_key)
            if value is None:
                continue

            # Check threshold
            if not _compare(float(value), rule.operator, rule.threshold):
                continue

            # Deduplication: skip if within cooldown window
            last = self._last_fired.get(rule.name, -999)
            if cycle - last < rule.cooldown_cycles:
                continue

            # Fire alert
            alert = Alert(
                rule_name=rule.name,
                severity=rule.severity,
                message=(
                    f"Rule '{rule.name}' triggered: {rule.metric}={value} "
                    f"{rule.operator.value} {rule.threshold}"
                ),
                data={"cycle": cycle, "metric": rule.metric, "value": value},
            )
            self._last_fired[rule.name] = cycle
            fired.append(alert)

            # Persist
            try:
                await self._db.insert_alert(
                    rule_name=alert.rule_name,
                    severity=alert.severity.value,
                    message=alert.message,
                    data_json=json.dumps(alert.data, ensure_ascii=False),
                    timestamp=alert.timestamp,
                )
            except Exception:
                logger.exception("Failed to persist alert for rule %s", rule.name)

            # Broadcast
            self._bus.publish_nowait(Event(
                type="alert",
                data={
                    "rule": rule.name,
                    "severity": alert.severity.value,
                    "message": alert.message,
                    "cycle": cycle,
                },
            ))

            logger.warning("Alert fired: %s", alert.message)

        return fired

    @staticmethod
    def _default_rules() -> list[Rule]:
        """Built-in rules for common operational concerns."""
        return [
            Rule(
                name="high_cost",
                metric="cost_usd",
                operator=Operator.GT,
                threshold=1.0,
                severity=AlertSeverity.WARNING,
                cooldown_cycles=10,
            ),
            Rule(
                name="cost_spike",
                metric="cost_usd",
                operator=Operator.GT,
                threshold=5.0,
                severity=AlertSeverity.CRITICAL,
                cooldown_cycles=5,
            ),
            Rule(
                name="slow_cycle",
                metric="duration_ms",
                operator=Operator.GT,
                threshold=120_000,  # 2 minutes
                severity=AlertSeverity.WARNING,
                cooldown_cycles=10,
            ),
            Rule(
                name="many_blockers",
                metric="blockers_detected",
                operator=Operator.GTE,
                threshold=3,
                severity=AlertSeverity.WARNING,
                cooldown_cycles=5,
            ),
        ]
