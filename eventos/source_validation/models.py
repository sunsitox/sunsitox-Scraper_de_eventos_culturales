"""Modelos de datos de la auditoría de fuentes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceCandidate:
    id: str
    name: str
    institution: str = ""
    source_type: str = ""
    coverage: str = ""
    region: str = ""
    commune: str = ""
    categories: str = ""
    channel: str = ""
    url: str = ""
    evidence_url: str = ""
    previous_status: str = ""
    eligible: bool = False
    priority: str = ""
    proposed_method: str = ""
    verified_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FetchObservation:
    requested_url: str = ""
    final_url: str = ""
    status_code: int = 0
    content_type: str = ""
    elapsed_seconds: float = 0.0
    bytes_read: int = 0
    title: str = ""
    visible_text: str = ""
    structured_event_count: int = 0
    date_signal_count: int = 0
    event_link_count: int = 0
    robots_allowed: bool | None = None
    error: str = ""


@dataclass(slots=True)
class RuleResult:
    name: str
    passed: bool | None
    points: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceValidationResult:
    source: SourceCandidate
    verdict: str
    score: int
    confidence: str
    checked_at: str
    observation: FetchObservation = field(default_factory=FetchObservation)
    rules: list[RuleResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "verdict": self.verdict,
            "score": self.score,
            "confidence": self.confidence,
            "checked_at": self.checked_at,
            "observation": asdict(self.observation),
            "rules": [rule.to_dict() for rule in self.rules],
            "reasons": self.reasons,
        }
