from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SignalTier = Literal[1, 2, 3, 4]
SignalKind = Literal["deterministic", "ambiguous"]


@dataclass(frozen=True)
class SignalDefinition:
    id: str
    tier: SignalTier
    name: str
    kind: SignalKind
    weight: float
    fix: str


@dataclass
class SignalFinding:
    id: str
    name: str
    tier: SignalTier
    weight: float
    flagged: bool
    confidence: float
    reason: str
    fix: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoreSummary:
    vibe_score: int
    humanness_score: int
    base_score: float
    cluster_bonus: int
    tier_counts: dict[int, int]


@dataclass
class AuditSnapshot:
    url: str
    title: str
    screenshot_path: str | None
    colors: dict[str, Any]
    fonts: list[str]
    body: dict[str, Any]
    buttons: list[dict[str, Any]]
    inputs: list[dict[str, Any]]
    links: list[dict[str, Any]]
    headings: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    images: list[dict[str, Any]]
    text: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditReport:
    url: str
    title: str
    score: ScoreSummary
    findings: list[SignalFinding]
    screenshot_path: str | None
    agent_notes: list[str] = field(default_factory=list)

