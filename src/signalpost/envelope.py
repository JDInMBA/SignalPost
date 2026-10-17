"""Typed company envelope, claims and evidence.

One terminal envelope is emitted per input organisation number, always, including
for companies where nothing was found or every source was blocked. Absence is
reported with an explicit availability state and never as a zero.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Availability(StrEnum):
    """The six states the evaluation contract allows."""

    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


class SourceClass(StrEnum):
    """Where a piece of evidence sits on the source ladder of the playbook."""

    OFFICIAL_REGISTRY = "official_registry"
    OFFICIAL_ACCOUNTS = "official_accounts"
    OFFICIAL_PUBLIC_BODY = "official_public_body"
    COMPANY_OWNED = "company_owned"
    LICENSED_API = "licensed_api"
    PERMITTED_PUBLIC_PAGE = "permitted_public_page"
    DERIVED = "derived"


class Evidence(BaseModel):
    """An immutable record of one retrieval that a claim can point at."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source_url: str
    source_class: SourceClass
    retrieved_at: str = Field(default_factory=utcnow)
    content_sha256: str | None = None
    http_status: int | None = None
    final_url: str | None = None
    redirect_chain: list[str] = Field(default_factory=list)
    claim_span: str | None = None
    extraction_method: str | None = None
    reporting_period: str | None = None
    effective_date: str | None = None
    licence: str | None = None
    access_policy: str | None = None

    @field_validator("claim_span")
    @classmethod
    def _trim_span(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        return v[:400]


class Claim(BaseModel):
    """One published fact about one company."""

    model_config = ConfigDict(extra="forbid")

    field: str
    family: str
    value: Any = None
    availability: Availability
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    as_of: str | None = None
    reporting_period: str | None = None
    note: str | None = None
    claim_key: str | None = None

    def key(self, organisation_number: str) -> str:
        """Stable identity of a claim across runs, so refresh can diff it."""
        if self.claim_key:
            return self.claim_key
        discriminator = ""
        if isinstance(self.value, dict):
            for k in ("id", "uuid", "url", "organisation_number", "year", "reference"):
                if self.value.get(k):
                    discriminator = str(self.value[k])
                    break
        if not discriminator and isinstance(self.value, str):
            discriminator = self.value
        digest = hashlib.sha256(discriminator.encode("utf-8")).hexdigest()[:16] if discriminator else ""
        return f"{organisation_number}:{self.field}" + (f":{digest}" if digest else "")


class Change(BaseModel):
    """A material difference between the previous snapshot and this run."""

    model_config = ConfigDict(extra="forbid")

    claim_key: str
    field: str
    change_type: Literal[
        "new_value", "changed_value", "removed_value", "new_role", "removed_role",
        "new_job", "closed_job", "new_location", "removed_location", "new_filing",
        "changed_description", "new_activity", "availability_change",
    ]
    materiality: Literal["material", "minor"] = "material"
    previous_value: Any = None
    current_value: Any = None
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    source: str | None = None
    kind: str
    detail: str | None = None
    retryable: bool = False


class Operations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int = 0
    cached_reads: int = 0
    bytes_downloaded: int = 0
    runtime_ms: int = 0
    third_party_cost_usd: float = 0.0
    strategies_used: list[str] = Field(default_factory=list)


class RunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_version: str
    started_at: str
    completed_at: str | None = None
    terminal_status: Literal["completed", "completed_with_errors", "failed"] = "completed"
    previous_snapshot_at: str | None = None


class CompanyEnvelope(BaseModel):
    """The terminal result for exactly one input organisation number."""

    model_config = ConfigDict(extra="forbid")

    organisation_number: str
    legal_name: str | None = None
    status: Availability = Availability.AVAILABLE
    run: RunMeta
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    errors: list[RunError] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, str] = Field(default_factory=dict)
    operations: Operations = Field(default_factory=Operations)

    def claim_fields(self) -> set[str]:
        return {c.field for c in self.claims}

    def evidence_index(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}

    def validate_integrity(self) -> list[str]:
        """Self-check run before writing. A failure here is a bug, not a data gap."""
        problems: list[str] = []
        known = self.evidence_index()
        for c in self.claims:
            if c.availability == Availability.AVAILABLE:
                if not c.evidence_ids:
                    problems.append(f"{c.field}: available with no evidence")
                if c.value is None:
                    problems.append(f"{c.field}: available with null value")
            for eid in c.evidence_ids:
                if eid not in known:
                    problems.append(f"{c.field}: dangling evidence id {eid}")
        seen: set[str] = set()
        for c in self.claims:
            k = c.key(self.organisation_number)
            if k in seen:
                problems.append(f"duplicate claim key {k}")
            seen.add(k)
        return problems
