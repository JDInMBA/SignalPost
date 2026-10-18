"""Exact-entity resolution gate.

A material wrong-company match is the one failure that stops a run becoming
official, so publication is deliberately conservative: a candidate is published
only when it clears one of four named tiers, and any conflicting organisation
number on the page is an absolute veto.

Tiers were calibrated against a held-out gold set built by hiding the registry
website of companies that have one and re-deriving it (see eval/gold.py).

Signals are position-aware. The legal name appearing anywhere in body text is
weak evidence - Norwegian pages routinely list partners, tenants and portfolio
companies. The same name in the title, og:site_name, a JSON-LD Organization name
or the footer is the page identifying itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .normalize import (
    email_domain,
    find_org_numbers,
    is_platform_host,
    looks_like_address,
    normalise_name,
    normalise_phone,
    normalise_text,
    registered_domain,
)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META = re.compile(
    r"<meta[^>]+(?:property|name)\s*=\s*[\"'](og:site_name|og:title|application-name)[\"'][^>]*"
    r"content\s*=\s*[\"']([^\"']{1,200})[\"']", re.I)
_META_REV = re.compile(
    r"<meta[^>]+content\s*=\s*[\"']([^\"']{1,200})[\"'][^>]*(?:property|name)\s*=\s*"
    r"[\"'](og:site_name|og:title|application-name)[\"']", re.I)
_JSONLD_NAME = re.compile(r'"@type"\s*:\s*"(?:Organization|LocalBusiness|Corporation|Store)"'
                          r'[^{}]*?"name"\s*:\s*"([^"]{1,160})"', re.S | re.I)
_JSONLD_NAME_REV = re.compile(r'"name"\s*:\s*"([^"]{1,160})"[^{}]*?"@type"\s*:\s*'
                              r'"(?:Organization|LocalBusiness|Corporation|Store)"', re.S | re.I)
_FOOTER = re.compile(r"<footer[^>]*>(.*?)</footer>", re.S | re.I)
_COPYRIGHT = re.compile(r"(?:©|&copy;|copyright)\s*[^<]{0,160}", re.I)

PARKED_MARKERS = (
    "domenet er til salgs", "this domain is for sale", "domain is for sale",
    "buy this domain", "kjøp dette domenet", "parkert hos", "parked domain",
    "domeneshop.no/parkert", "under construction", "under konstruksjon",
    "coming soon", "kommer snart", "default web site page", "it works!",
    "apache2 ubuntu default page", "welcome to nginx", "sedo", "afternic",
)


class Tier(StrEnum):
    """Why a candidate was published, in descending strength."""

    ORG_NUMBER = "A_org_number_on_page"
    REGISTRY_WEBSITE = "B_registry_declared_website"
    REGISTRY_EMAIL = "C_registry_email_domain_plus_corroboration"
    NAME_AND_LOCATOR = "D_site_identity_name_plus_locator"
    REJECTED = "rejected"


@dataclass(slots=True)
class Candidate:
    """A domain under consideration, with the pages fetched for it."""

    domain: str
    channel: str
    url: str = ""
    pages: list[tuple[str, str]] = field(default_factory=list)  # (url, html)


@dataclass(slots=True)
class Verdict:
    """The outcome of the gate for one candidate."""

    accepted: bool
    tier: Tier
    confidence: float
    signals: dict[str, str]
    conflicts: list[str]
    reasons: list[str]
    proof_url: str | None = None
    proof_span: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted, "tier": str(self.tier),
            "confidence": self.confidence, "signals": self.signals,
            "conflicting_organisation_numbers": self.conflicts,
            "reasons": self.reasons, "proof_url": self.proof_url,
        }


def site_identity_strings(html: str) -> list[str]:
    """Strings where a page states who it is, as opposed to who it mentions."""
    out: list[str] = []
    if m := _TITLE.search(html):
        out.append(m.group(1))
    for rx, grp in ((_META, 2), (_META_REV, 1)):
        out.extend(m.group(grp) for m in rx.finditer(html))
    for rx in (_JSONLD_NAME, _JSONLD_NAME_REV):
        out.extend(m.group(1) for m in rx.finditer(html))
    for m in _FOOTER.finditer(html):
        out.append(m.group(1))
    out.extend(m.group(0) for m in _COPYRIGHT.finditer(html))
    return [normalise_text(s) for s in out if s]


def looks_parked(html: str) -> bool:
    text = normalise_text(html)
    if len(text) < 180 and not _TITLE.search(html):
        return True
    return any(marker in text for marker in PARKED_MARKERS)


class IdentityGate:
    """Decides whether a candidate domain is the exact legal entity."""

    def __init__(self, *, min_tier_d_enabled: bool = True) -> None:
        self.tier_d_enabled = min_tier_d_enabled

    def evaluate(self, entity: dict[str, Any], candidate: Candidate) -> Verdict:
        org = str(entity.get("organisation_number") or entity.get("organisasjonsnummer") or "")
        signals: dict[str, str] = {}
        reasons: list[str] = []

        if not candidate.pages:
            return Verdict(False, Tier.REJECTED, 0.0, {}, [], ["no page retrieved"])
        if is_platform_host(candidate.domain):
            return Verdict(False, Tier.REJECTED, 0.0, {}, [],
                           ["candidate is a platform profile, not a company-owned site"])

        home_html = candidate.pages[0][1]
        if looks_parked(home_html) and len(candidate.pages) == 1:
            return Verdict(False, Tier.REJECTED, 0.0, {}, [],
                           ["page is parked, for sale or a default server page"])

        full_text = " ".join(normalise_text(h) for _, h in candidate.pages)
        digits_only = re.sub(r"\D", "", full_text)
        identity_blob = " ".join(
            s for _, h in candidate.pages for s in site_identity_strings(h))

        # --- veto: a different company's organisation number -------------
        found: set[str] = set()
        proof_url = proof_span = None
        for url, html in candidate.pages:
            page_orgs = find_org_numbers(normalise_text(html))
            found |= page_orgs
            if org in page_orgs and proof_url is None:
                proof_url = url
                proof_span = _span_around(normalise_text(html), org)
        conflicts = sorted(o for o in found if o != org)

        if org in found:
            signals["org_number_on_page"] = org

        # --- registry-attested domain links ------------------------------
        #
        # A candidate may redirect somewhere else, and what gets published is the
        # domain the crawl landed on. Signals are therefore matched against the
        # landed domain, never the requested one: the registry attesting
        # stilbygg.no says nothing about norgeshus.no, the franchise portal that
        # stilbygg.no redirects to.
        cand_domain = registered_domain(candidate.domain)
        landed_domain = registered_domain(candidate.pages[0][0]) or cand_domain
        published_domain = landed_domain or cand_domain
        redirected = bool(landed_domain) and landed_domain != cand_domain
        if redirected:
            signals["redirected_to"] = landed_domain

        declared_site = registered_domain(entity.get("website") or entity.get("hjemmeside") or "")
        if declared_site and declared_site == published_domain:
            signals["registry_declared_website"] = declared_site
        mail_domain = email_domain(entity.get("email") or entity.get("epostadresse"))
        if mail_domain and mail_domain == published_domain:
            signals["registry_email_domain"] = mail_domain

        # --- corroborating locators --------------------------------------
        legal_name = entity.get("legal_name") or entity.get("navn") or ""
        normalised = normalise_name(legal_name)
        name_is_address = looks_like_address(legal_name)
        if normalised and len(normalised) >= 4 and not name_is_address:
            if normalised in identity_blob:
                signals["legal_name_in_site_identity"] = normalised
            elif normalised in full_text:
                signals["legal_name_in_body"] = normalised
        elif name_is_address:
            reasons.append("legal name is address-shaped; name matching disabled")

        for field_name in ("phone", "mobile", "telefon", "mobil"):
            phone = normalise_phone(entity.get(field_name))
            if phone and phone in digits_only:
                signals["registry_phone"] = phone
                break

        address = entity.get("business_address") or entity.get("forretningsadresse") or {}
        street = " ".join(address.get("adresse") or [])
        street_n = normalise_name(street)
        if len(street_n) >= 6 and street_n in full_text:
            signals["registered_street"] = street
        postcode, city = address.get("postnummer"), (address.get("poststed") or "").lower()
        if postcode and city and postcode in full_text and city in full_text:
            signals["postcode_and_city"] = f"{postcode} {city}"

        # --- decision -----------------------------------------------------
        if conflicts:
            reasons.append(
                f"page carries other valid organisation numbers {conflicts[:3]}; "
                "publishing would risk a wrong-company match")
            if "org_number_on_page" not in signals:
                return Verdict(False, Tier.REJECTED, 0.0, signals, conflicts, reasons)
            reasons.append("own organisation number also present, which overrides the conflict")

        has_name_identity = "legal_name_in_site_identity" in signals
        locators = {"registry_phone", "registered_street", "postcode_and_city"} & signals.keys()

        if "org_number_on_page" in signals:
            return Verdict(True, Tier.ORG_NUMBER, 0.99, signals, conflicts,
                           reasons + ["organisation number printed on the page"],
                           proof_url, proof_span)
        if "registry_declared_website" in signals:
            return Verdict(True, Tier.REGISTRY_WEBSITE, 0.95, signals, conflicts,
                           reasons + ["domain is the website the company filed with the registry"],
                           candidate.pages[0][0])

        # A redirect that leaves the candidate's registrable domain has landed on
        # something the entity does not attest. Chains, franchise portals and
        # parent brands all absorb their members' domains this way, so without the
        # organisation number on the page or the registry naming this exact
        # domain, abstain rather than publish the destination.
        if redirected:
            reasons.append(
                f"{cand_domain} redirects to {landed_domain}, which this entity does "
                "not attest; a chain or parent portal cannot be published as the "
                "company's own site")
            return Verdict(False, Tier.REJECTED, 0.0, signals, conflicts, reasons)

        # The company has told the registry which domain is its website. A
        # different domain that cannot print this organisation number is not
        # publishable, whatever else corroborates it. Measured on the validation
        # split: this is what separates ENVION GROUP AS (registry site
        # smartbloc.no, registry e-mail mail@fjordkick.no) from its founder's
        # other venture.
        if declared_site and declared_site != published_domain:
            reasons.append(
                f"the registry declares {declared_site} as this company's website; "
                f"{landed_domain} cannot be published without its organisation number")
            return Verdict(False, Tier.REJECTED, 0.0, signals, conflicts, reasons)

        # A single locator is too weak on its own. A founder's mobile number, a
        # shared registered address and an accountant's office are all common
        # across unrelated Norwegian companies, so corroboration needs either the
        # site naming itself or two independent locators.
        if "registry_email_domain" in signals and (has_name_identity or len(locators) >= 2):
            return Verdict(True, Tier.REGISTRY_EMAIL, 0.88, signals, conflicts,
                           reasons + ["domain matches the registry e-mail domain and the page "
                                      "corroborates the entity"], candidate.pages[0][0])
        if self.tier_d_enabled and has_name_identity and locators:
            return Verdict(True, Tier.NAME_AND_LOCATOR, 0.80, signals, conflicts,
                           reasons + ["site identifies itself by the legal name and carries a "
                                      "registered locator"], candidate.pages[0][0])

        if signals:
            reasons.append("signals present but below the publication threshold")
        else:
            reasons.append("no entity signal found on the candidate")
        return Verdict(False, Tier.REJECTED, 0.0, signals, conflicts, reasons)


def _span_around(text: str, needle: str, width: int = 120) -> str:
    grouped = f"{needle[:3]} {needle[3:6]} {needle[6:]}"
    for probe in (needle, grouped, f"{needle[:3]}.{needle[3:6]}.{needle[6:]}"):
        idx = text.find(probe)
        if idx >= 0:
            start = max(0, idx - width // 2)
            return text[start:idx + len(probe) + width // 2].strip()
    return ""
