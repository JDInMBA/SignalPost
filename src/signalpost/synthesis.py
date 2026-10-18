"""Decision-useful synthesis.

The summary answers what the product is for: what the company does, who leads it,
where it operates, how its last filed year looks, whether it appears to be hiring,
and what dated public activity the checked sources show.

Every sentence is assembled from claims that already carry evidence. Nothing here
introduces a fact, infers a number, or softens an unknown: a field that was not
found is named as not found, with the reason.
"""
from __future__ import annotations

from typing import Any

from .envelope import Availability, Claim, CompanyEnvelope

_NOK = "NOK"


def _by_field(claims: list[Claim]) -> dict[str, Claim]:
    return {c.field: c for c in claims}


def _available(claim: Claim | None) -> Any:
    if claim is None or claim.availability != Availability.AVAILABLE:
        return None
    return claim.value


def _fmt_money(value: Any, currency: str | None = None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    unit = currency or _NOK
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value/1_000_000_000:,.2f}bn {unit}"
    if magnitude >= 1_000_000:
        return f"{value/1_000_000:,.1f}m {unit}"
    if magnitude >= 1_000:
        return f"{value/1_000:,.0f}k {unit}"
    return f"{value:,.0f} {unit}"


def build(envelope: CompanyEnvelope) -> dict[str, Any]:
    """Compose the summary block for one company."""
    claims = _by_field(envelope.claims)
    identity = _available(claims.get("legal_identity")) or {}
    status = _available(claims.get("operating_status")) or {}
    purpose = _available(claims.get("stated_purpose"))
    site_desc = _available(claims.get("website_description")) or {}
    accounts = _available(claims.get("annual_accounts")) or []
    leaders = _available(claims.get("leadership")) or []
    workplaces = _available(claims.get("registered_workplaces")) or []
    jobs_claim = claims.get("job_postings")
    jobs_value = _available(jobs_claim)
    jobs = jobs_value if isinstance(jobs_value, list) else []

    open_positions = (
        len(jobs)
        if jobs_claim is not None
        and jobs_claim.availability == Availability.AVAILABLE
        else None
    )
    website = _available(claims.get("official_website")) or {}
    profiles = _available(claims.get("company_owned_profiles")) or []
    activity = _available(claims.get("company_published_activity")) or []
    announcements = _available(claims.get("registry_announcements")) or []
    employees = _available(claims.get("registered_employees")) or {}

    name = identity.get("legal_name") or envelope.legal_name or envelope.organisation_number
    headline: list[str] = []

    # What it is
    form = identity.get("legal_form_label") or identity.get("legal_form")
    industry = (identity.get("industry") or [{}])[0].get("label")
    place = identity.get("municipality")
    bits = [b for b in (form, industry) if b]
    if bits:
        article = "an" if bits[0][:1].lower() in "aeiou" else "a"
        headline.append(
            f"{name} is {article} {bits[0].lower()}"
            + (f" in {bits[1].lower()}" if len(bits) > 1 else "")
            + (f", registered in {place.title()}." if place else "."))
    else:
        headline.append(f"{name} is registered in the Norwegian entity register.")

    # What it does, preferring the company's own words over the statute
    if site_desc.get("description"):
        headline.append(f"Its website describes it as: {site_desc['description'][:240]}")
    elif purpose:
        headline.append(f"Its registered statutory purpose is: {purpose[:240]}")

    # How the latest filed year looks
    financial_line = None
    if accounts:
        latest = max(accounts, key=lambda a: str(a.get("period_to") or ""))
        revenue = _fmt_money(latest.get("revenue"), latest.get("currency"))
        result = _fmt_money(latest.get("annual_result"), latest.get("currency"))
        equity = _fmt_money(latest.get("equity"), latest.get("currency"))
        period = latest.get("period_to")
        parts = [p for p in (
            f"revenue {revenue}" if revenue else None,
            f"annual result {result}" if result else None,
            f"equity {equity}" if equity else None) if p]
        if parts:
            financial_line = (f"For the period ending {period}, the filed accounts report "
                              + ", ".join(parts) + ".")
            headline.append(financial_line)

    # Who leads it
    if leaders:
        principal = next((p for p in leaders if p.get("role_code") in {"DAGL", "LEDE"}), leaders[0])
        headline.append(f"{principal.get('role', 'A registered role holder')} is "
                        f"{principal.get('name')}; {len(leaders)} active registered "
                        f"role{'s' if len(leaders) != 1 else ''} in total.")

    # Where it operates
    if workplaces:
        headline.append(f"It has {len(workplaces)} registered workplace"
                        f"{'s' if len(workplaces) != 1 else ''} in the register.")

    # Whether it is hiring
    if jobs:
        headline.append(f"It is advertising {len(jobs)} open position"
                        f"{'s' if len(jobs) != 1 else ''} on the public job service.")

    # Adverse status is never buried
    adverse = []
    if status.get("bankrupt"):
        adverse.append(f"registered bankrupt{(' on ' + status['bankruptcy_date']) if status.get('bankruptcy_date') else ''}")
    if status.get("under_liquidation"):
        adverse.append("under liquidation")
    if status.get("compulsory_liquidation"):
        adverse.append("under compulsory liquidation")
    if adverse:
        headline.append("Registry status: " + "; ".join(adverse) + ".")

    # Honest unknowns, with the reason each one is unknown
    unknowns: list[dict[str, str]] = []
    for field, label in (
        ("official_website", "verified official website"),
        ("company_owned_profiles", "company-owned social profiles"),
        ("job_postings", "job advertisements"),
        ("annual_accounts", "filed annual accounts"),
        ("leadership", "registered leadership"),
        ("registered_workplaces", "registered workplaces"),
        ("company_published_activity", "dated activity on the company site"),
        ("registry_announcements", "official registry announcements"),
    ):
        claim = claims.get(field)
        if claim is None:
            unknowns.append({"field": label, "state": "not_checked",
                             "reason": "no connector ran for this family"})
        elif claim.availability != Availability.AVAILABLE:
            unknowns.append({"field": label, "state": str(claim.availability),
                             "reason": claim.note or "source returned no record"})

    return {
        "headline": " ".join(headline),
        "what_it_does": site_desc.get("description") or purpose,
        "latest_filed_period": (accounts[0].get("period_to") if accounts else None),
        "financial_line": financial_line,
        "employees_registered": employees.get("count") if employees else None,
        "leaders": [{"name": p.get("name"), "role": p.get("role")} for p in leaders[:6]],
        "verified_website": website.get("url"),
        "profiles": [p.get("url") for p in profiles],
        "open_positions": open_positions,
        "recent_activity": (activity[:5] + announcements[:5])[:5],
        "changes_since_last_run": [
            {"field": c.field, "type": c.change_type, "materiality": c.materiality}
            for c in envelope.changes
        ],
        "unknowns": unknowns,
        "evidence_count": len(envelope.evidence),
        "note": ("Every statement above is drawn from a claim in this envelope and can be "
                 "traced to its evidence record. Fields listed under unknowns were checked "
                 "and not found, or could not be checked; none were assumed to be zero."),
    }
