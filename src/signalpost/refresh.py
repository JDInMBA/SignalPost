"""Refresh as a diff.

A refresh compares the current run against the stored previous snapshot and emits
typed changes. Three properties matter and are tested:

1. Idempotence - re-running the same source snapshot produces zero changes.
2. Preservation - a failed or blocked source keeps the last supported value and
   exposes the failure, rather than deleting the claim or writing a zero.
3. Attribution - a change carries the evidence for both the previous and the
   current side.
"""
from __future__ import annotations

import json
from typing import Any

from .envelope import Availability, Change, Claim, CompanyEnvelope

# Fields where a difference is a business event rather than cosmetic noise.
MATERIAL_FIELDS: frozenset[str] = frozenset({
    "legal_identity", "operating_status", "official_website", "leadership",
    "registered_workplaces", "annual_accounts", "annual_account_filings",
    "job_postings", "company_owned_profiles", "registered_employees",
    "group_structure", "registry_announcements", "public_brand_aliases",
})

CHANGE_TYPE_BY_FIELD: dict[str, str] = {
    "leadership": "new_role",
    "registered_workplaces": "new_location",
    "job_postings": "new_job",
    "annual_account_filings": "new_filing",
    "annual_accounts": "new_filing",
    "company_published_activity": "new_activity",
    "registry_announcements": "new_activity",
    "website_description": "changed_description",
}

# Volatile keys that must not be compared, or every run would look changed.
_VOLATILE_KEYS: frozenset[str] = frozenset({
    "retrieved_at", "as_of", "verification", "confidence", "evidence_ids",
})


def canonical(value: Any) -> Any:
    """Order-independent, volatility-free form of a claim value."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in sorted(value.items()) if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        rendered = [canonical(v) for v in value]
        try:
            return sorted(rendered, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
        except TypeError:
            return rendered
    return value


def _digest(value: Any) -> str:
    return json.dumps(canonical(value), sort_keys=True, ensure_ascii=False, default=str)


def _identity_of(item: Any) -> str:
    """Stable identity of one element inside a list-valued claim."""
    if isinstance(item, dict):
        for key in ("uuid", "url", "organisation_number", "year", "id", "name", "reference"):
            if item.get(key):
                return f"{key}={item[key]}"
    return _digest(item)


def diff(previous: CompanyEnvelope | None, current: CompanyEnvelope) -> list[Change]:
    """Typed, deduplicated changes between two runs of the same company."""
    if previous is None:
        return []
    org = current.organisation_number
    before = {c.field: c for c in previous.claims}
    after = {c.field: c for c in current.claims}
    changes: list[Change] = []

    for field in sorted(before.keys() | after.keys()):
        old, new = before.get(field), after.get(field)
        if old is None and new is None:
            continue

        # A source that failed this time must not erase a value we already hold.
        if new is not None and new.availability in {Availability.FAILED, Availability.BLOCKED}:
            if old is not None and old.availability == Availability.AVAILABLE:
                changes.append(Change(
                    claim_key=old.key(org), field=field, change_type="availability_change",
                    materiality="minor", previous_value=old.availability,
                    current_value=new.availability,
                    first_observed_at=old.as_of, last_observed_at=new.as_of,
                    evidence_ids=list(new.evidence_ids)))
            continue

        if old is None:
            if new.availability == Availability.AVAILABLE:
                changes.append(Change(
                    claim_key=new.key(org), field=field,
                    change_type=CHANGE_TYPE_BY_FIELD.get(field, "new_value"),
                    materiality="material" if field in MATERIAL_FIELDS else "minor",
                    current_value=new.value, last_observed_at=new.as_of,
                    evidence_ids=list(new.evidence_ids)))
            continue

        if new is None:
            if old.availability == Availability.AVAILABLE:
                changes.append(Change(
                    claim_key=old.key(org), field=field, change_type="removed_value",
                    materiality="material" if field in MATERIAL_FIELDS else "minor",
                    previous_value=old.value, first_observed_at=old.as_of,
                    evidence_ids=list(old.evidence_ids)))
            continue

        if _digest(old.value) == _digest(new.value):
            if old.availability != new.availability:
                changes.append(Change(
                    claim_key=new.key(org), field=field, change_type="availability_change",
                    materiality="minor", previous_value=old.availability,
                    current_value=new.availability, first_observed_at=old.as_of,
                    last_observed_at=new.as_of, evidence_ids=list(new.evidence_ids)))
            continue

        if isinstance(old.value, list) and isinstance(new.value, list):
            changes.extend(_list_changes(org, field, old, new))
            continue

        changes.append(Change(
            claim_key=new.key(org), field=field,
            change_type=CHANGE_TYPE_BY_FIELD.get(field, "changed_value"),
            materiality="material" if field in MATERIAL_FIELDS else "minor",
            previous_value=old.value, current_value=new.value,
            first_observed_at=old.as_of, last_observed_at=new.as_of,
            evidence_ids=list(new.evidence_ids)))
    return changes


def _list_changes(org: str, field: str, old: Claim, new: Claim) -> list[Change]:
    """Element-level additions and removals inside a list-valued claim."""
    added_type = CHANGE_TYPE_BY_FIELD.get(field, "new_value")
    removed_type = {
        "new_role": "removed_role", "new_location": "removed_location",
        "new_job": "closed_job",
    }.get(added_type, "removed_value")

    before = {_identity_of(i): i for i in (old.value or [])}
    after = {_identity_of(i): i for i in (new.value or [])}
    materiality = "material" if field in MATERIAL_FIELDS else "minor"
    out: list[Change] = []
    for key in sorted(after.keys() - before.keys()):
        out.append(Change(claim_key=f"{org}:{field}:{key}", field=field,
                          change_type=added_type, materiality=materiality,
                          current_value=after[key], last_observed_at=new.as_of,
                          evidence_ids=list(new.evidence_ids)))
    for key in sorted(before.keys() - after.keys()):
        out.append(Change(claim_key=f"{org}:{field}:{key}", field=field,
                          change_type=removed_type, materiality=materiality,
                          previous_value=before[key], first_observed_at=old.as_of,
                          evidence_ids=list(old.evidence_ids)))
    return out


def carry_forward(previous: CompanyEnvelope | None, current: CompanyEnvelope) -> None:
    """Keep the last supported value when this run could not reach a source.

    The failure stays visible on the claim; the value is restored so a transient
    outage does not look like the company losing a website or a board.

    The evidence records the restored claim points at are copied across with it.
    Carrying the ids alone would leave every restored claim referencing evidence
    that is not in this envelope, which `validate_integrity` rejects and which
    would make the claim unverifiable by anyone reading the output.
    """
    if previous is None:
        return
    before = {c.field: c for c in previous.claims}
    prior_evidence = {e.id: e for e in previous.evidence}
    present = {e.id for e in current.evidence}
    for claim in current.claims:
        if claim.availability not in {Availability.FAILED, Availability.BLOCKED}:
            continue
        old = before.get(claim.field)
        if old is None or old.availability != Availability.AVAILABLE or old.value is None:
            continue
        claim.value = old.value
        claim.evidence_ids = list(old.evidence_ids)
        for eid in claim.evidence_ids:
            carried = prior_evidence.get(eid)
            if carried is not None and eid not in present:
                current.evidence.append(carried)
                present.add(eid)
        claim.confidence = min(claim.confidence, 0.6)
        claim.note = (f"source unreachable on this run ({claim.availability}); "
                      f"last supported value retained from {old.as_of or 'the previous run'}")
        claim.as_of = old.as_of
