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


# How the website match was proved, in words a reader can judge.
CONFIDENCE_BY_TIER: dict[str, dict[str, str]] = {
    "A_org_number_on_page": {
        "level": "high",
        "basis": "the company's organisation number is printed on the page"},
    "B_registry_declared_website": {
        "level": "high",
        "basis": "this is the website the company filed with the registry"},
    "C_registry_email_domain_plus_corroboration": {
        "level": "medium",
        "basis": "the domain matches the company's registered e-mail domain and the "
                 "page carries a registered phone, address or the legal name"},
    "D_site_identity_name_plus_locator": {
        "level": "low",
        "basis": "the site names the company and carries a registered locator, but "
                 "neither the organisation number nor a registry-declared domain"},
}


def website_confidence(envelope: CompanyEnvelope) -> dict[str, Any]:
    """Plain-language confidence for the website match, and what proved it."""
    claim = next((c for c in envelope.claims if c.field == "official_website"), None)
    if claim is None:
        return {"level": "none", "basis": "no website connector ran"}
    if claim.availability != Availability.AVAILABLE:
        return {"level": "none", "state": str(claim.availability),
                "basis": claim.note or "no website published"}
    tier = ((claim.value or {}).get("verification") or {}).get("tier", "")
    info = CONFIDENCE_BY_TIER.get(tier, {"level": "low", "basis": "match basis not recorded"})
    if (claim.value or {}).get("site_names_this_company") is False:
        info = {"level": "low",
                "basis": ("the site does not identify this company by name, so it may "
                          "belong to a parent group or chain; its profiles, news and "
                          "jobs are not published as this company's")}
    return {**info, "tier": tier, "score": claim.confidence,
            "discovery_channel": ((claim.value or {}).get("verification") or {})
            .get("discovery_channel")}


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

    # How the latest filed year looks, and where it sits against earlier years.
    financial_line = None
    trend_line = None
    history: list[dict[str, Any]] = []
    if accounts:
        ordered = sorted(accounts, key=lambda a: str(a.get("period_to") or ""), reverse=True)
        history = [{"period_to": a.get("period_to"), "revenue": a.get("revenue"),
                    "operating_result": a.get("operating_result"),
                    "annual_result": a.get("annual_result"), "equity": a.get("equity"),
                    "total_assets": a.get("total_assets"),
                    "currency": a.get("currency")} for a in ordered]
        latest = ordered[0]
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

        # A single year says little; the trend is what a reader is deciding on.
        series = [(a.get("period_to"), a.get("revenue")) for a in ordered
                  if isinstance(a.get("revenue"), (int, float))]
        if len(series) >= 2:
            (new_p, new_v), (old_p, old_v) = series[0], series[-1]
            spans = " to ".join(str(p)[:4] for p in (old_p, new_p))
            if old_v:
                pct = (new_v - old_v) / abs(old_v) * 100
                shape = "grew" if pct > 1 else "fell" if pct < -1 else "was flat"
                trend_line = (f"Across the {len(series)} filed years {spans}, revenue "
                              f"{shape}" + (f" by {abs(pct):.0f}%" if abs(pct) >= 1 else "")
                              + f", from {_fmt_money(old_v, latest.get('currency'))} to "
                              f"{_fmt_money(new_v, latest.get('currency'))}.")
            else:
                trend_line = (f"Filed accounts cover {len(series)} years ({spans}); the "
                              f"earliest reports no revenue figure, so no trend is asserted.")
            headline.append(trend_line)

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
        "latest_filed_period": (history[0]["period_to"] if history else None),
        "financial_line": financial_line,
        "financial_trend": trend_line,
        "financial_history": history,
        "employees_registered": employees.get("count") if employees else None,
        "leaders": [{"name": p.get("name"), "role": p.get("role")} for p in leaders[:6]],
        "verified_website": website.get("url"),
        "website_confidence": website_confidence(envelope),
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
