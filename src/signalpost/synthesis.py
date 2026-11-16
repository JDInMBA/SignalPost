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
    "R_registry_declared_site_not_reached": {
        "level": "medium",
        "basis": "the company filed this website with the registry; no page of it "
                 "could be read on this run, so the site itself has not been "
                 "checked against the company"},
    "D_site_identity_name_plus_locator": {
        "level": "low",
        "basis": "the site names the company and carries a registered locator, but "
                 "neither the organisation number nor a registry-declared domain "
                 "confirms the match"},
}


def _is_verified(website: dict[str, Any]) -> bool:
    """Was the site itself checked, rather than taken from the register alone?"""
    tier = ((website or {}).get("verification") or {}).get("tier") or ""
    return bool(website) and not tier.startswith("R_")


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


# Preferred order for the trend: what a reader looks at first, then what a
# company with no trading activity still files.
TREND_METRICS: tuple[tuple[str, str], ...] = (
    ("revenue", "revenue"),
    ("total_assets", "total assets"),
    ("equity", "equity"),
)

# What the register files when no industry was given. Treated as absent.
UNSPECIFIED_INDUSTRY = {"uoppgitt", "ikke oppgitt", "uspesifisert"}

# A move this large or larger is worth opening on. Below it the size of the
# latest year tells a reader more than its direction does.
MATERIAL_MOVE_PCT = 20.0

# The registers a lead fact can come from, named in the lead itself. A fact a
# buyer is expected to act on has to be attributable without reading further.
ACCOUNTS_REGISTER = "Regnskapsregisteret"
ENTITY_REGISTER = "Enhetsregisteret"
JOB_SERVICE = "arbeidsplassen.nav.no"


def _trend(ordered: list[dict[str, Any]], currency: str | None) -> dict[str, Any] | None:
    """The direction of travel across the filed years, as facts rather than prose.

    The lead fact and the narrative sentence both need this. Measuring it once
    here and rendering it twice is what stops the two from disagreeing about
    which metric moved, or by how much.

    A metric filed as nil in every year states no direction, so the next metric
    is tried first. Holding and pre-trading companies file nil revenue while
    their balance sheet still moves. Nil is reported only when no later metric
    moves either, where nil is itself the finding.
    """
    nil_fallback: dict[str, Any] | None = None
    for key, label in TREND_METRICS:
        series = [(a.get("period_to"), a.get(key)) for a in ordered
                  if isinstance(a.get(key), (int, float))]
        if len(series) < 2:
            continue
        (new_p, new_v), (old_p, old_v) = series[0], series[-1]
        base = {
            "metric": label, "years": len(series),
            "span": " to ".join(str(p)[:4] for p in (old_p, new_p)),
            "old": old_v, "new": new_v,
            "old_text": _fmt_money(old_v, currency),
            "new_text": _fmt_money(new_v, currency),
        }
        if all(v == 0 for _, v in series):
            if nil_fallback is None:
                nil_fallback = {**base, "shape": "nil", "pct": None}
            continue
        # A percentage across a sign change is misleading, and so is one off a
        # zero base, so those shapes carry no pct and are stated in amounts.
        if old_v > 0 and new_v > 0:
            pct = (new_v - old_v) / old_v * 100
            shape = "flat" if abs(pct) < 1 else ("grew" if pct > 0 else "fell")
            return {**base, "shape": shape, "pct": pct}
        if old_v > 0 >= new_v:
            return {**base, "shape": "turned_negative", "pct": None}
        if old_v <= 0 < new_v:
            return {**base, "shape": "recovered", "pct": None}
        return {**base, "shape": "both_negative", "pct": None}
    return nil_fallback


def _trend_sentence(trend: dict[str, Any] | None) -> str | None:
    """One sentence on the direction of travel, for the body of the summary."""
    if not trend:
        return None
    head = f"Across the {trend['years']} filed years {trend['span']}, {trend['metric']}"
    shape = trend["shape"]
    if shape == "nil":
        return f"{head} was filed as nil in every year."
    if shape == "flat":
        return f"{head} was flat at about {trend['new_text']}."
    if shape in {"grew", "fell"}:
        return (f"{head} {shape} by {abs(trend['pct']):.0f}%, from "
                f"{trend['old_text']} to {trend['new_text']}.")
    if shape == "turned_negative":
        return (f"{head} fell from {trend['old_text']} to {trend['new_text']}, "
                f"turning negative.")
    if shape == "recovered":
        return f"{head} recovered from {trend['old_text']} to {trend['new_text']}."
    return (f"{head} moved from {trend['old_text']} to {trend['new_text']}; neither "
            f"year is positive, so no percentage is given.")


def _join(parts: list[str]) -> str:
    """Join a short list the way a sentence would."""
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


def _clip(text: str, limit: int = 240) -> str:
    """Shorten free text without cutting a word in half.

    A hard slice ended one summary on "men er ikke begrenset til kjop," with a
    trailing comma and no mark that anything had been removed, which reads as a
    sentence the company never wrote rather than as a quotation that was cut.
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return f"{head}\u2026"


def _quote(text: str) -> str:
    """Clip quoted free text and close it, so the next sentence cannot run on.

    Companies write site descriptions without a full stop. Quoted mid-paragraph
    the next sentence joined on to the end of it: "Garn og tilbehor The same
    filing also reports ...".
    """
    clipped = _clip(text)
    if not clipped:
        return ""
    return clipped if clipped[-1:] in ".!?\u2026" else f"{clipped}."


def _on_revenue(revenue: Any, currency: str | None) -> str:
    """The turnover a figure should be read against, or nothing to read it against.

    A filed nil is a fact and is said as one. Rendering it as "on revenue of
    0 NOK" reads as a rounding artefact rather than as what the filing states.
    """
    if revenue == 0:
        return ", on nil filed revenue"
    scale = _fmt_money(revenue, currency)
    return f", on revenue of {scale}" if scale else ""


def _lead_trend(name: str, trend: dict[str, Any]) -> str:
    """The trend as an opening fact, with the register that holds it."""
    shape, span = trend["shape"], trend["span"]
    years, metric = trend["years"], trend["metric"]
    if shape == "nil":
        return (f"{name} has filed nil {metric} with {ACCOUNTS_REGISTER} in every one "
                f"of its {years} filed years, {span}.")
    opening = f"The accounts {name} filed with {ACCOUNTS_REGISTER} show {metric}"
    tail = f"across its {years} filed years, {span}"
    if shape in {"grew", "fell"}:
        moving = "growing" if shape == "grew" else "falling"
        return (f"{opening} {moving} {abs(trend['pct']):.0f}% {tail}, from "
                f"{trend['old_text']} to {trend['new_text']}.")
    if shape == "turned_negative":
        return (f"{opening} falling from {trend['old_text']} to {trend['new_text']} "
                f"{tail}, turning negative.")
    if shape == "recovered":
        return (f"{opening} recovering from {trend['old_text']} to "
                f"{trend['new_text']} {tail}.")
    return (f"{opening} moving from {trend['old_text']} to {trend['new_text']} "
            f"{tail}, with neither year positive.")


def _lead_fact(
        name: str,
        adverse: list[str],
        latest: dict[str, Any] | None,
        trend: dict[str, Any] | None,
        accounts_claim: Claim | None,
        open_positions: int | None,
) -> dict[str, Any] | None:
    """The one fact to open with, and the register it came from.

    Ranked by what a reader deciding whether to deal with this company reads
    first. A company the register has wound up outranks any figure it ever
    filed. A balance sheet that owes more than it owns outranks a growth rate.
    A growth rate outranks the size of a single year. Anything in the accounts
    outranks an activity signal.

    The previous summary opened with legal form, industry, municipality and the
    statutory purpose, which is the register's own ordering, not a reader's: the
    one fact worth acting on sat four sentences down. Each lead here names the
    register it came from, so the opening claim can be checked on its own.
    """
    if adverse:
        return {"text": f"{name} is flagged in {ENTITY_REGISTER}: {'; '.join(adverse)}.",
                "source": ENTITY_REGISTER, "basis": "registry_status", "states": set()}

    latest = latest or {}
    period, currency = latest.get("period_to"), latest.get("currency")
    equity, revenue = latest.get("equity"), latest.get("revenue")

    # Negative equity is the fact a counterparty most wants in front of them,
    # and it is the one the register states plainly. Revenue rides along
    # because the size of the shortfall cannot be read without the scale.
    if isinstance(equity, (int, float)) and equity < 0 and period:
        on_revenue = _on_revenue(revenue, currency)
        return {"text": (f"The accounts {name} filed with {ACCOUNTS_REGISTER} show equity "
                         f"of {_fmt_money(equity, currency)} for the year to {period}"
                         + on_revenue
                         + ", so its filed liabilities exceed its filed assets."),
                "source": ACCOUNTS_REGISTER, "basis": "negative_equity",
                "states": {"equity"} | ({"revenue"} if on_revenue else set())}

    if trend:
        shape, pct = trend["shape"], trend["pct"]
        if shape in {"turned_negative", "recovered", "both_negative", "nil"} or (
                pct is not None and abs(pct) >= MATERIAL_MOVE_PCT):
            # The trend quotes the latest year of its own metric, so that
            # figure is already in front of the reader.
            quoted = {"revenue": "revenue", "equity": "equity"}.get(trend["metric"])
            return {"text": _lead_trend(name, trend),
                    "source": ACCOUNTS_REGISTER, "basis": f"trend_{shape}",
                    "states": {quoted} if quoted else set()}

    # A year that lost money is a fact in its own right, but it sits below the
    # multi-year move: a 10k loss at a dormant company is a weaker opening than
    # the 65% fall in its assets, and the smaller fact would displace the larger
    # one on every company that filed either. Where the loss leads, the trend
    # follows in the next sentence, and the other way round.
    result = latest.get("annual_result")
    if isinstance(result, (int, float)) and result < 0 and period:
        on_revenue = _on_revenue(revenue, currency)
        return {"text": (f"The accounts {name} filed with {ACCOUNTS_REGISTER} show a loss "
                         f"of {_fmt_money(abs(result), currency)} for the year to {period}"
                         + on_revenue + "."),
                "source": ACCOUNTS_REGISTER, "basis": "loss_making",
                "states": {"annual_result"} | ({"revenue"} if on_revenue else set())}

    figures = [p for p in (
        f"revenue of {_fmt_money(revenue, currency)}" if _fmt_money(revenue, currency) else None,
        (f"an annual result of {_fmt_money(latest.get('annual_result'), currency)}"
         if _fmt_money(latest.get("annual_result"), currency) else None),
        f"equity of {_fmt_money(equity, currency)}" if _fmt_money(equity, currency) else None,
    ) if p]
    if figures and period:
        return {"text": (f"The accounts {name} filed with {ACCOUNTS_REGISTER} report "
                         f"{_join(figures)} for the year to {period}."),
                "source": ACCOUNTS_REGISTER, "basis": "latest_year",
                "states": {"revenue", "annual_result", "equity"}}

    if open_positions:
        return {"text": (f"{name} is advertising {open_positions} open position"
                         f"{'s' if open_positions != 1 else ''} on the public job "
                         f"service {JOB_SERVICE}."),
                "source": JOB_SERVICE, "basis": "open_positions", "states": set()}

    # No figures at all is itself the fact a reader needs, and the reason the
    # accounts are missing is part of it.
    if accounts_claim is None or accounts_claim.availability != Availability.AVAILABLE:
        reason = ((accounts_claim.note if accounts_claim is not None else
                   "no connector ran for the accounts register") or "").strip().rstrip(".")
        return {"text": (f"{ACCOUNTS_REGISTER} holds no annual accounts for {name}, so "
                         f"there are no filed figures to judge it on"
                         + (f" ({reason})." if reason else ".")),
                "source": ACCOUNTS_REGISTER, "basis": "no_accounts", "states": set()}
    return None


def build(envelope: CompanyEnvelope) -> dict[str, Any]:
    """Compose the summary block for one company."""
    claims = _by_field(envelope.claims)
    identity = _available(claims.get("legal_identity")) or {}
    status = _available(claims.get("operating_status")) or {}
    purpose = _available(claims.get("stated_purpose"))
    reported_activity = _available(claims.get("registry_activity"))
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

    # Adverse status is never buried, which is why it is measured before the
    # headline is composed rather than appended at the end of it.
    adverse = []
    if status.get("bankrupt"):
        adverse.append("registered bankrupt" + (
            f" on {status['bankruptcy_date']}" if status.get("bankruptcy_date") else ""))
    if status.get("under_liquidation"):
        adverse.append("under liquidation")
    if status.get("compulsory_liquidation"):
        adverse.append("under compulsory liquidation")

    # The latest filed year, and where it sits against the earlier ones.
    financial_line = None
    figures: dict[str, str] = {}
    trend = None
    history: list[dict[str, Any]] = []
    latest: dict[str, Any] = {}
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
        figures = {k: v for k, v in (
            ("revenue", f"revenue {revenue}" if revenue else None),
            ("annual_result", f"annual result {result}" if result else None),
            ("equity", f"equity {equity}" if equity else None)) if v}
        if figures:
            financial_line = (f"For the period ending {latest.get('period_to')}, the filed "
                              f"accounts report " + ", ".join(figures.values()) + ".")
        # A single year says little; the trend is what a reader is deciding on.
        # Most of the register is holding and property companies that file no
        # revenue at all, so this falls through to the balance sheet rather
        # than reporting no trend for them.
        trend = _trend(ordered, latest.get("currency"))
    trend_line = _trend_sentence(trend)

    lead = _lead_fact(name, adverse, latest, trend, claims.get("annual_accounts"),
                      open_positions)
    basis = (lead or {}).get("basis")
    headline: list[str] = []
    if lead:
        headline.append(lead["text"])

    # What it is. The register's own description of the company is context for
    # the lead fact, not the opening of the summary, so it follows. Once the
    # lead has named the company, repeating the name here would read as a
    # second introduction.
    subject = "It" if lead else name
    form = identity.get("legal_form_label") or identity.get("legal_form")
    industry = (identity.get("industry") or [{}])[0].get("label")
    # The register files "uoppgitt" where no industry was given. Reading it back
    # as one produced "an aksjeselskap in uoppgitt", which states a line of
    # business the filing explicitly declines to state.
    if (industry or "").strip().lower() in UNSPECIFIED_INDUSTRY:
        industry = None
    place = identity.get("municipality")
    bits = [b for b in (form, industry) if b]
    if bits:
        article = "an" if bits[0][:1].lower() in "aeiou" else "a"
        headline.append(
            f"{subject} is {article} {bits[0].lower()}"
            + (f" in {bits[1].lower()}" if len(bits) > 1 else "")
            + (f", registered in {place.title()}." if place else "."))
    else:
        headline.append(f"{subject} is registered in the Norwegian entity register.")

    # What it does, preferring the company's own words over the statute. A field
    # that holds only whitespace is not words the company wrote, so it is tested
    # after clipping rather than before.
    described = _quote(site_desc.get("description") or "")
    reported = _quote(reported_activity or "")
    stated = _quote(purpose or "")
    if described:
        headline.append(f"Its website describes it as: {described}")
    elif reported:
        headline.append(f"It reports its activity to the register as: {reported}")
    elif stated:
        headline.append(f"Its registered statutory purpose is: {stated}")

    # The figures, minus whatever the lead already said. Restating the lead two
    # sentences later is how the summary came to read as a recital of the
    # registry fields in the first place, so the body states only what the lead
    # left out. `financial_line` itself stays whole: it is a field of its own,
    # read by consumers that never see this prose.
    stated = set((lead or {}).get("states") or ())
    remaining = [v for k, v in figures.items() if k not in stated]
    if remaining and len(remaining) == len(figures):
        headline.append(financial_line)
    elif remaining:
        headline.append(f"The same filing also reports {', '.join(remaining)}.")
    if trend_line and not (basis or "").startswith("trend_"):
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
    if jobs and basis != "open_positions":
        headline.append(f"It is advertising {len(jobs)} open position"
                        f"{'s' if len(jobs) != 1 else ''} on the public job service.")

    if adverse and basis != "registry_status":
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
        # The lead is carried as its own field as well as at the head of the
        # prose, so a reader or a table can take the one fact without parsing
        # the paragraph, and so the register behind it travels with it.
        "lead": (lead or {}).get("text"),
        "lead_source": (lead or {}).get("source"),
        "lead_basis": basis,
        "what_it_does": (site_desc.get("description")
                         or reported_activity or purpose),
        "latest_filed_period": (history[0]["period_to"] if history else None),
        "financial_line": financial_line,
        "financial_trend": trend_line,
        "financial_history": history,
        "employees_registered": employees.get("count") if employees else None,
        "leaders": [{"name": p.get("name"), "role": p.get("role")} for p in leaders[:6]],
        # A site published on the register's word alone has not been verified
        # against the company, so it must not answer to a key called
        # `verified_website`. A reader skimming the summary would take the name
        # for the check, which is the overclaim this field exists to avoid.
        "verified_website": (website.get("url") if _is_verified(website) else None),
        "registry_declared_website": (None if _is_verified(website)
                                      else website.get("url")),
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
