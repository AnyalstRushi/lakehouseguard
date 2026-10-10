"""Incident model + store (append-only JSONL, de-duplicated per run)."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .storage import Lakehouse

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
INCIDENT_TYPES = (
    "schema_break", "contract_violation", "freshness_breach", "volume_anomaly",
    "null_rate_drift", "distribution_drift", "high_heal_rate", "schema_drift",
)


@dataclass
class Incident:
    run_id: str
    ts: str
    type: str
    severity: str
    dataset: str
    title: str
    evidence: dict[str, Any] = field(default_factory=dict)
    column: str | None = None
    status: str = "open"
    id: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            raise ValueError(f"bad severity {self.severity}")
        if not self.id:
            self.id = "INC-" + hashlib.sha1(f"{self.fingerprint}|{self.run_id}".encode()).hexdigest()[:8].upper()

    @property
    def fingerprint(self) -> str:
        return f"{self.type}|{self.dataset}|{self.column or '*'}|{self.evidence.get('rule', '')}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IncidentStore:
    FILE = "incidents.jsonl"

    def __init__(self, lake: Lakehouse):
        self.lake = lake

    def add(self, incident: Incident) -> bool:
        """Persist unless an identical incident (same id) already exists. Returns True if new."""
        if any(r["id"] == incident.id for r in self.lake.read_jsonl(self.FILE)):
            return False
        self.lake.append_jsonl(self.FILE, incident.to_dict())
        return True

    def all(self) -> list[Incident]:
        return [Incident(**r) for r in self.lake.read_jsonl(self.FILE)]

    def for_run(self, run_id: str) -> list[Incident]:
        return [i for i in self.all() if i.run_id == run_id]

    def get(self, incident_id: str) -> Incident | None:
        for i in self.all():
            if i.id == incident_id:
                return i
        return None

    def recurrence(self, incident: Incident) -> int:
        """How many times has this fingerprint fired (including this one)?"""
        return sum(1 for i in self.all() if i.fingerprint == incident.fingerprint)

    def resolve(self, incident_id: str) -> bool:
        records = self.lake.read_jsonl(self.FILE)
        hit = False
        for r in records:
            if r["id"] == incident_id and r["status"] != "resolved":
                r["status"] = "resolved"
                hit = True
        if hit:
            self.lake.rewrite_jsonl(self.FILE, records)
        return hit
