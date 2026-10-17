"""Brønnøysundregistrene - the identity anchor and the official families.

Open data under NLOD 2.0, no key and no registration. The organisation number is
the stable key for the whole system; every other source in this agent is joined
back to it rather than to a name.

A 404 from an official endpoint is evidence that no record is returned, which is
reported as `not_available`, never as a zero or an empty success.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..envelope import Availability, Claim, Evidence, SourceClass
from ..http import Fetcher, Response

BASE = "https://data.brreg.no"
ENTITY = BASE + "/enhetsregisteret/api/enheter/{org}"
ROLES = BASE + "/enhetsregisteret/api/enheter/{org}/roller"
GROUP = BASE + "/enhetsregisteret/api/konsernstruktur/{org}"
SUBUNITS = BASE + "/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=100"
ACCOUNTS = BASE + "/regnskapsregisteret/regnskap/{org}"
ACCOUNT_YEARS = BASE + "/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
ACCOUNT_PDF = BASE + "/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"

LICENCE = "NLOD 2.0 (https://data.norge.no/nlod/en/2.0)"
_JSON = "application/json"

# The annual-account copy endpoint publishes a ~30 starts/minute allowance.
_history_lock = threading.Lock()
_history_last = 0.0


def _reserve_history_slot() -> None:
    global _history_last
    with _history_lock:
        wait = 2.1 - (time.monotonic() - _history_last)
        if wait > 0:
            time.sleep(wait)
        _history_last = time.monotonic()


def _dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _evidence(eid: str, resp: Response, source_class: SourceClass,
              *, span: str | None = None, method: str = "json_field",
              reporting_period: str | None = None) -> Evidence:
    return Evidence(
        id=eid,
        source_url=resp.url,
        source_class=source_class,
        retrieved_at=resp.retrieved_at,
        content_sha256=resp.content_sha256,
        http_status=resp.status,
        final_url=resp.final_url or resp.url,
        redirect_chain=resp.redirect_chain,
        claim_span=span,
        extraction_method=method,
        reporting_period=reporting_period,
        licence=LICENCE,
        access_policy="documented open API, no authentication required",
    )


def _state(resp: Response) -> Availability:
    if resp.ok:
        return Availability.AVAILABLE
    if resp.status in {404, 410}:
        return Availability.NOT_AVAILABLE
    if resp.blocked:
        return Availability.BLOCKED
    return Availability.FAILED


def _empty_state(resp: Response) -> Availability:
    """State for a request that succeeded but returned no records.

    An HTTP 200 carrying an empty list means the register was asked and holds
    nothing, which is `not_available`. Reporting it as `available` with a null
    value would be a claim with no content behind it.
    """
    if resp.ok:
        return Availability.NOT_AVAILABLE
    return _state(resp)


def _json(resp: Response) -> Any:
    try:
        return json.loads(resp.text or resp.body.decode("utf-8", "replace"))
    except Exception:
        return None


class BrregClient:
    """Fetches the six official endpoints and turns them into claims."""

    def __init__(self, fetcher: Fetcher) -> None:
        self.http = fetcher

    def _get(self, url: str, *, history: bool = False) -> Response:
        if history:
            _reserve_history_slot()
        return self.http.fetch(url, accept=_JSON, retries=2)

    # -- identity ---------------------------------------------------------
    def entity(self, org: str) -> tuple[dict[str, Any] | None, Response]:
        resp = self._get(ENTITY.format(org=org))
        return (_json(resp) if resp.ok else None), resp

    def identity_claims(self, org: str, body: dict[str, Any] | None,
                        resp: Response, eid: str) -> tuple[list[Claim], list[Evidence]]:
        ev = [_evidence(eid, resp, SourceClass.OFFICIAL_REGISTRY,
                        span=(body or {}).get("navn"))]
        state = _state(resp)
        if not body:
            return [Claim(field="legal_identity", family="identity", value=None,
                          availability=state, evidence_ids=[eid],
                          note="registry returned no entity record")], ev

        addr = body.get("forretningsadresse") or {}
        postal = body.get("postadresse") or {}
        identity = {
            "organisation_number": body.get("organisasjonsnummer"),
            "legal_name": body.get("navn"),
            "legal_form": _dig(body, "organisasjonsform", "kode"),
            "legal_form_label": _dig(body, "organisasjonsform", "beskrivelse"),
            "registered_at": body.get("registreringsdatoEnhetsregisteret"),
            "founded_at": body.get("stiftelsesdato"),
            "industry": [
                {"code": _dig(body, f"naeringskode{i}", "kode"),
                 "label": _dig(body, f"naeringskode{i}", "beskrivelse")}
                for i in (1, 2, 3) if _dig(body, f"naeringskode{i}", "kode")
            ],
            "sector_code": _dig(body, "institusjonellSektorkode", "kode"),
            "business_address": addr or None,
            "postal_address": postal or None,
            "municipality": addr.get("kommune"),
            "municipality_number": addr.get("kommunenummer"),
            "in_business_register": body.get("registrertIForetaksregisteret"),
            "in_vat_register": body.get("registrertIMvaregisteret"),
            "in_foundation_register": body.get("registrertIStiftelsesregisteret"),
            "in_voluntary_register": body.get("registrertIFrivillighetsregisteret"),
            "language_form": body.get("maalform"),
            "share_capital": body.get("kapital"),
            "part_of_group": body.get("erIKonsern"),
            "parent_organisation_number": body.get("overordnetEnhet"),
        }
        claims = [Claim(field="legal_identity", family="identity", value=identity,
                        availability=Availability.AVAILABLE, evidence_ids=[eid],
                        as_of=resp.retrieved_at)]

        # Operating status is a separate decision-relevant fact.
        claims.append(Claim(
            field="operating_status", family="identity",
            value={
                "bankrupt": body.get("konkurs"),
                "bankruptcy_date": body.get("konkursdato"),
                "under_liquidation": body.get("underAvvikling"),
                "liquidation_date": body.get("underAvviklingDato"),
                "compulsory_liquidation": body.get("underTvangsavviklingEllerTvangsopplosning"),
            },
            availability=Availability.AVAILABLE, evidence_ids=[eid], as_of=resp.retrieved_at))

        # Public brand candidates: historical names are registry-attested aliases.
        aliases = [h.get("navn") for h in (body.get("historiskeNavn") or []) if h.get("navn")]
        claims.append(Claim(
            field="public_brand_aliases", family="identity", value=aliases or None,
            availability=Availability.AVAILABLE if aliases else Availability.NOT_AVAILABLE,
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if aliases else "registry records no former names"))

        # Statutory purpose: an official description of what the company does,
        # present for 96.8% of the universe where a website exists for only 10.9%.
        purpose = body.get("vedtektsfestetFormaal") or body.get("aktivitet") or []
        text = " ".join(purpose).strip() if isinstance(purpose, list) else str(purpose)
        claims.append(Claim(
            field="stated_purpose", family="identity", value=text or None,
            availability=Availability.AVAILABLE if text else Availability.NOT_AVAILABLE,
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if text else "no statutory purpose recorded"))

        # Registry-reported contact points. These double as website candidates.
        contact = {
            "website": body.get("hjemmeside") or None,
            "email": body.get("epostadresse") or None,
            "phone": body.get("telefon") or None,
            "mobile": body.get("mobil") or None,
        }
        has_contact = any(contact.values())
        claims.append(Claim(
            field="registry_contact", family="contact", value=contact if has_contact else None,
            availability=Availability.AVAILABLE if has_contact else Availability.NOT_AVAILABLE,
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if has_contact else "registry holds no contact details"))

        employees = body.get("antallAnsatte")
        has_employee_record = bool(body.get("harRegistrertAntallAnsatte"))
        employee_count_known = has_employee_record and employees is not None

        claims.append(Claim(
            field="registered_employees", family="workforce",
            value={"count": employees,
                   "registered_at": body.get("registreringsdatoAntallAnsatteEnhetsregisteret"),
                   "source_register": "NAV Aa-registeret"} if has_employee_record else None,
            availability=(Availability.AVAILABLE if employee_count_known
                          else Availability.NOT_AVAILABLE),
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=(None if employee_count_known else
                  "employee record exists but count is unknown" if has_employee_record else
                  "no employee count registered; this is not a count of zero")))
        return claims, ev

    # -- roles ------------------------------------------------------------
    def roles(self, org: str, eid: str) -> tuple[list[Claim], list[Evidence]]:
        resp = self._get(ROLES.format(org=org))
        ev = [_evidence(eid, resp, SourceClass.OFFICIAL_REGISTRY, method="json_role_groups")]
        body = _json(resp)
        people: list[dict[str, Any]] = []
        for group in (body or {}).get("rollegrupper", []) if isinstance(body, dict) else []:
            for item in group.get("roller", []) or []:
                person = item.get("person") or {}
                unit = item.get("enhet") or {}
                name_parts = (person.get("navn") or {})
                display = " ".join(filter(None, (
                    name_parts.get("fornavn"), name_parts.get("mellomnavn"),
                    name_parts.get("etternavn")))) or unit.get("navn")
                if not display:
                    continue
                people.append({
                    "name": display,
                    "role_code": _dig(item, "type", "kode"),
                    "role": _dig(item, "type", "beskrivelse"),
                    "group_code": _dig(group, "type", "kode"),
                    "group": _dig(group, "type", "beskrivelse"),
                    "is_entity": bool(unit.get("organisasjonsnummer")),
                    "entity_organisation_number": unit.get("organisasjonsnummer"),
                    "resigned": bool(item.get("fratraadt") or item.get("avregistrert")),
                    "last_changed": group.get("sistEndret"),
                })
        # Birth dates are deliberately discarded: only the company-centric role is used.
        active = [p for p in people if not p["resigned"]]
        return [Claim(
            field="leadership", family="leadership", value=active or None,
            availability=Availability.AVAILABLE if active else _empty_state(resp),
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if active else "no active registered roles returned")], ev

    # -- registered workplaces -------------------------------------------
    def subunits(self, org: str, eid: str) -> tuple[list[Claim], list[Evidence]]:
        resp = self._get(SUBUNITS.format(org=org))
        ev = [_evidence(eid, resp, SourceClass.OFFICIAL_REGISTRY, method="json_embedded_list")]
        body = _json(resp)
        rows = _dig(body, "_embedded", "underenheter") or []
        locations = [{
            "organisation_number": r.get("organisasjonsnummer"),
            "name": r.get("navn"),
            "address": r.get("beliggenhetsadresse") or r.get("postadresse"),
            "industry": _dig(r, "naeringskode1", "beskrivelse"),
            "industry_code": _dig(r, "naeringskode1", "kode"),
            "employees": r.get("antallAnsatte") if r.get("harRegistrertAntallAnsatte") else None,
            "website": r.get("hjemmeside") or None,
            "registered_at": r.get("registreringsdatoEnhetsregisteret"),
        } for r in rows]
        return [Claim(
            field="registered_workplaces", family="locations", value=locations or None,
            availability=Availability.AVAILABLE if locations else _empty_state(resp),
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if locations else "registry lists no subunits for this entity")], ev

    # -- group structure --------------------------------------------------
    def group(self, org: str, eid: str) -> tuple[list[Claim], list[Evidence]]:
        resp = self._get(GROUP.format(org=org))
        ev = [_evidence(eid, resp, SourceClass.OFFICIAL_REGISTRY, method="json_group")]
        body = _json(resp) if resp.ok else None
        return [Claim(
            field="group_structure", family="group", value=body or None,
            availability=Availability.AVAILABLE if body else _empty_state(resp),
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if body else "no official group relationship returned")], ev

    # -- annual accounts --------------------------------------------------
    def accounts(self, org: str, eid: str) -> tuple[list[Claim], list[Evidence]]:
        resp = self._get(ACCOUNTS.format(org=org))
        body = _json(resp)
        records = body if isinstance(body, list) else []
        # Regnskapsregisteret returns filings oldest-first. Publishing records[0]
        # as "the latest accounts" would report a stale year for every company, so
        # order explicitly by the end of the reporting period, newest first.
        records = sorted(
            records,
            key=lambda r: str(_dig(r, "regnskapsperiode", "tilDato") or ""),
            reverse=True,
        )
        period = _dig(records[0], "regnskapsperiode", "tilDato") if records else None
        ev = [_evidence(eid, resp, SourceClass.OFFICIAL_ACCOUNTS,
                        method="json_normalised_accounts", reporting_period=period)]
        rows = []
        for item in records[:5]:
            rows.append({
                "record_id": item.get("id"),
                "account_type": item.get("regnskapstype"),
                "period_from": _dig(item, "regnskapsperiode", "fraDato"),
                "period_to": _dig(item, "regnskapsperiode", "tilDato"),
                "currency": item.get("valuta"),
                "revenue": _dig(item, "resultatregnskapResultat", "driftsresultat",
                                "driftsinntekter", "sumDriftsinntekter"),
                "operating_result": _dig(item, "resultatregnskapResultat", "driftsresultat",
                                         "driftsresultat"),
                "profit_before_tax": _dig(item, "resultatregnskapResultat",
                                          "ordinaertResultatFoerSkattekostnad"),
                "annual_result": _dig(item, "resultatregnskapResultat", "aarsresultat"),
                "total_assets": _dig(item, "eiendeler", "sumEiendeler"),
                "equity": _dig(item, "egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
                "total_debt": _dig(item, "egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
                "audited": item.get("revisjon"),
            })
        # A null figure stays null. Converting it to 0 would fabricate a financial value.
        return [Claim(
            field="annual_accounts", family="financials", value=rows or None,
            availability=Availability.AVAILABLE if rows else _empty_state(resp),
            evidence_ids=[eid], as_of=resp.retrieved_at, reporting_period=period,
            note=None if rows else "no normalised annual accounts returned for this entity")], ev

    def account_history(self, org: str, eid: str) -> tuple[list[Claim], list[Evidence]]:
        resp = self._get(ACCOUNT_YEARS.format(org=org), history=True)
        ev = [_evidence(eid, resp, SourceClass.OFFICIAL_ACCOUNTS, method="json_year_list")]
        body = _json(resp)
        years = sorted({str(y) for y in body if str(y).isdigit()}, reverse=True) if isinstance(body, list) else []
        value = [{"year": y, "filing_copy_url": ACCOUNT_PDF.format(org=org, year=y)} for y in years]
        return [Claim(
            field="annual_account_filings", family="financials", value=value or None,
            availability=Availability.AVAILABLE if value else _empty_state(resp),
            evidence_ids=[eid], as_of=resp.retrieved_at,
            note=None if value else "no filing copies listed")], ev
