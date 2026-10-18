"""Hiring signals from Nav's public job service (arbeidsplassen.nav.no).

Why this source carries the hiring family on its own:

* every published advertisement page embeds the advertiser's organisation number,
  so a posting is bound to the exact legal entity rather than to a name;
* Nav attaches advertisements to the registered subunit that is hiring, which this
  module resolves back to the parent entity and labels rather than collapses;
* robots.txt on arbeidsplassen.nav.no is `Disallow:` (empty), i.e. the whole site
  is open to crawlers, and the search endpoint is documented and unauthenticated.

An optional organisation-number index built from Nav's official change feed is
used when present. It is a declared cache: it only nominates advertisements, and
every published posting is re-fetched live so the evidence is first-hand.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import quote

from ..envelope import Availability, Claim, Evidence, SourceClass
from ..http import Fetcher, Response
from ..normalize import normalise_name

SEARCH = "https://arbeidsplassen.nav.no/stillinger/api/search?size={size}&q={q}"
SEARCH_PAGE = "https://arbeidsplassen.nav.no/stillinger/api/search?size={size}&from={offset}"
AD_PAGE = "https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
FEED_TOKEN_URL = "https://pam-stilling-feed.nav.no/api/publicToken"
FEED = "https://pam-stilling-feed.nav.no/api/v1/feed"
FEED_ENTRY = "https://pam-stilling-feed.nav.no/api/v1/feedentry/{uuid}"

LICENCE = "Nav Arbeidsplassen API terms (https://arbeidsplassen.nav.no/vilkar-api)"

# The advertisement page is a server-rendered React payload, so its fields appear
# as backslash-escaped JSON inside the HTML.
def _field(name: str, value: str = r'[^"\\]{1,160}') -> re.Pattern[str]:
    return re.compile(rf'\\?"{name}\\?"\s*:\s*\\?"({value})')


ORGNR_IN_PAGE = _field("orgnr", r"\d{9}")
JOBTITLE_IN_PAGE = _field("jobtitle")
TITLE_IN_PAGE = _field("title")
PUBLISHED_IN_PAGE = _field("published", r"\d{4}-\d{2}-\d{2}")
EXPIRES_IN_PAGE = _field("expires", r"\d{4}-\d{2}-\d{2}")
MUNICIPAL_IN_PAGE = _field("municipal")
ENGAGEMENT_IN_PAGE = _field("engagementtype")
SECTOR_IN_PAGE = _field("sector")

# Strings the page shows when an advertisement is gone, which must not be
# mistaken for a job title.
_UI_NOISE = ("vi fant dessverre", "fant ikke", "siden finnes ikke", "not found")


def _evidence(eid: str, resp: Response, *, span: str | None = None,
              method: str = "embedded_json") -> Evidence:
    return Evidence(
        id=eid, source_url=resp.url, source_class=SourceClass.OFFICIAL_PUBLIC_BODY,
        retrieved_at=resp.retrieved_at, content_sha256=resp.content_sha256,
        http_status=resp.status, final_url=resp.final_url or resp.url,
        redirect_chain=resp.redirect_chain, claim_span=span,
        extraction_method=method, licence=LICENCE,
        access_policy="public job service, robots.txt permits all agents",
    )


class NavJobsClient:
    """Finds advertisements and proves each one belongs to the exact entity."""

    def __init__(self, fetcher: Fetcher, *, index: dict[str, list[str]] | None = None,
                 max_ads: int = 12, max_candidates: int = 25,
                 max_search_terms: int = 2, live_search_budget: int = 0) -> None:
        self.http = fetcher
        self.index = index or {}
        self.max_ads = max_ads
        self.max_candidates = max_candidates
        # Each search term is a rate-limited request against a host that answers
        # 429 under load, so the number of terms is a budget decision.
        self.max_search_terms = max_search_terms
        # Populated by build_run_snapshot(); empty means fall back to live search.
        self.snapshot: dict[str, list[str]] = {}
        self.snapshot_meta: dict[str, Any] = {}
        self.snapshot_blocked = False
        self.snapshot_attempted = False
        # Run-wide ceiling on live per-company searches. Nav rate-limits hard, so
        # an unbounded fallback is what turned a 100-company run into two hours.
        self._live_budget = live_search_budget

    # -- run-level snapshot -------------------------------------------------
    def build_run_snapshot(self, *, max_pages: int = 400, page_size: int = 25,
                           verbose: bool = False) -> dict[str, Any]:
        """Page the national active-advertisement list once for the whole batch.

        Per-company search does not scale. `arbeidsplassen.nav.no` enforces a
        politeness floor and answers 429 under load, so one query per company costs
        a 1,000-company run many hours and the run is then not scored at all.

        This instead pages the active list once - a fixed cost independent of batch
        size - and matches employers locally by normalised name. A name match is
        only a candidate: the advertisement page is still fetched and its
        organisation number re-read before anything is published.
        """
        by_name: dict[str, list[str]] = {}
        seen: set[str] = set()
        offset = 0
        pages = 0
        total: int | None = None
        blocked = False
        while pages < max_pages:
            resp = self.http.fetch(SEARCH_PAGE.format(size=page_size, offset=offset),
                                   accept="application/json", retries=2)
            if not resp.ok:
                blocked = blocked or resp.blocked
                break
            try:
                body = json.loads(resp.text)
                hits = body["hits"]["hits"]
                total = body["hits"]["total"]["value"]
            except Exception:
                break
            if not hits:
                break
            for hit in hits:
                uuid = hit.get("_id")
                src = hit.get("_source") or {}
                if not uuid or uuid in seen:
                    continue
                seen.add(uuid)
                for raw in ((src.get("employer") or {}).get("name"), src.get("businessName")):
                    key = _match_key(raw)
                    if key:
                        by_name.setdefault(key, []).append(uuid)
            offset += len(hits)
            pages += 1
            if verbose and pages % 40 == 0:
                print(f"  nav snapshot: {len(seen)} advertisements, {len(by_name)} names")
        self.snapshot = by_name
        # A snapshot that returned nothing because the source refused is a very
        # different fact from a snapshot that genuinely found nothing, and the
        # hiring family must report the difference rather than claim "no jobs".
        self.snapshot_blocked = blocked
        self.snapshot_attempted = True
        self.snapshot_meta = {"advertisements": len(seen), "distinct_employer_names": len(by_name),
                              "pages": pages, "reported_total_active": total,
                              "blocked": self.snapshot_blocked,
                              "complete": bool(total is not None and len(seen) >= total)}
        return self.snapshot_meta

    # -- candidate generation ---------------------------------------------
    def _search(self, term: str, size: int = 25) -> tuple[list[dict[str, Any]], Response]:
        resp = self.http.fetch(SEARCH.format(size=size, q=quote(term)),
                               accept="application/json", retries=1)
        if not resp.ok:
            return [], resp
        try:
            body = json.loads(resp.text)
        except Exception:
            return [], resp
        return [h.get("_source", {}) | {"uuid": h.get("_id")}
                for h in body.get("hits", {}).get("hits", [])], resp

    def candidate_uuids(self, legal_name: str, aliases: Iterable[str] = (),
                        subunit_names: Iterable[str] = ()) -> tuple[list[str], list[Response]]:
        """Advertisement ids worth verifying, from the index then from search."""
        uuids: list[str] = []
        responses: list[Response] = []
        seen: set[str] = set()

        terms: list[str] = []
        for term in [legal_name, *aliases, *subunit_names]:
            cleaned = re.sub(r"\s+AVD\s+.*$", "", (term or "").strip(), flags=re.I)
            if len(cleaned) >= 3 and cleaned.lower() not in {t.lower() for t in terms}:
                terms.append(cleaned)
        for term in terms[: self.max_search_terms]:
            hits, resp = self._search(term)
            responses.append(resp)
            for hit in hits:
                uid = hit.get("uuid")
                if uid and uid not in seen:
                    seen.add(uid)
                    uuids.append(uid)
            if len(uuids) >= self.max_candidates:
                break
        return uuids[: self.max_candidates], responses

    # -- exact-entity verification ----------------------------------------
    def verify_ad(self, uuid: str) -> tuple[dict[str, Any] | None, Response]:
        """Fetch one advertisement and read its advertiser organisation number."""
        resp = self.http.fetch(AD_PAGE.format(uuid=uuid))
        if not resp.ok or not resp.text:
            return None, resp
        m = ORGNR_IN_PAGE.search(resp.text)
        if not m:
            return None, resp
        return {
            "uuid": uuid,
            "advertiser_organisation_number": m.group(1),
            "title": _first_clean(JOBTITLE_IN_PAGE, resp.text) or _first_clean(TITLE_IN_PAGE, resp.text),
            "published": _first(PUBLISHED_IN_PAGE, resp.text),
            "expires": _first(EXPIRES_IN_PAGE, resp.text),
            "municipality": _first_clean(MUNICIPAL_IN_PAGE, resp.text),
            "engagement_type": _first_clean(ENGAGEMENT_IN_PAGE, resp.text),
            "sector": _first_clean(SECTOR_IN_PAGE, resp.text),
            "url": resp.final_url or resp.url,
        }, resp

    # -- the family ---------------------------------------------------------
    def collect(self, org: str, legal_name: str, *, aliases: Iterable[str] = (),
                subunits: Iterable[dict[str, Any]] = (), eid_prefix: str = "ev-jobs",
                ) -> tuple[list[Claim], list[Evidence]]:
        subunit_list = list(subunits)
        sub_orgs = {str(s.get("organisation_number")): s.get("name")
                    for s in subunit_list if s.get("organisation_number")}
        accepted_orgs = {org} | set(sub_orgs)

        uuids = list(dict.fromkeys(self.index.get(org, [])))
        search_responses: list[Response] = []

        if self.snapshot_blocked:
            return [
                Claim(field="job_postings", family="hiring", value=None,
                      availability=Availability.BLOCKED,
                      note="the public job service refused the request for this run "
                           "(HTTP 429); no hiring conclusion is drawn"),
                Claim(field="hiring_signal", family="hiring", value=None,
                      availability=Availability.BLOCKED,
                      note="hiring source unavailable this run"),
            ], []
        snapshot_incomplete = (
            self.snapshot_attempted
            and not self.snapshot_meta.get("complete", False)
        )
        if self.snapshot:
            # Name match against the run snapshot. Candidates only - every one is
            # still proved against the advertisement page below.
            for term in [legal_name, *aliases, *(str(s.get("name") or "") for s in subunit_list)]:
                key = _match_key(term)
                if not key:
                    continue
                for uuid in self.snapshot.get(key, []):
                    if uuid not in uuids:
                        uuids.append(uuid)
        # Live per-company search is the last resort. It is the only unbounded
        # path in the agent, so it runs only when neither the shipped index nor a
        # run snapshot could offer a candidate, and only within a run-wide budget.
        searched_live = False
        if not uuids and not self.snapshot and self._live_budget > 0:
            self._live_budget -= 1
            searched_live = True
            found, search_responses = self.candidate_uuids(
                legal_name, aliases, [str(s.get("name") or "") for s in subunit_list])
            uuids.extend(u for u in found if u not in uuids)

        # Did this company actually get looked up anywhere?
        consulted = bool(self.index) or bool(self.snapshot) or searched_live

        evidence: list[Evidence] = []
        jobs: list[dict[str, Any]] = []
        rejected = 0
        blocked = any(r.blocked for r in search_responses)

        for uuid in uuids[: self.max_candidates]:
            if len(jobs) >= self.max_ads:
                break
            ad, resp = self.verify_ad(uuid)
            if ad is None:
                if resp.blocked:
                    blocked = True
                continue
            advertiser = ad["advertiser_organisation_number"]
            if advertiser not in accepted_orgs:
                rejected += 1
                continue
            eid = f"{eid_prefix}-{len(jobs)}"
            evidence.append(_evidence(
                eid, resp, span=f"orgnr {advertiser} - {ad.get('title') or ''}".strip()))
            jobs.append({
                "title": ad.get("title"),
                "url": ad["url"],
                "published": ad.get("published"),
                "expires": ad.get("expires"),
                "municipality": ad.get("municipality"),
                "engagement_type": ad.get("engagement_type"),
                "sector": ad.get("sector"),
                "advertiser_organisation_number": advertiser,
                # A subunit advertisement is labelled, never merged into the parent.
                "advertised_by": ("this entity" if advertiser == org
                                  else f"registered subunit {advertiser} ({sub_orgs.get(advertiser)})"),
            })

        if jobs:
            availability, note = Availability.AVAILABLE, None
        elif blocked:
            availability, note = Availability.BLOCKED, "Nav job service refused the request"
        elif snapshot_incomplete:
            availability = Availability.NOT_AVAILABLE
            note = (
                "NAV active-advertisement snapshot was incomplete; "
                "no verified advertisements were found among the candidates checked, "
                "but absence of jobs cannot be concluded"
            )
        elif uuids:
            availability = Availability.NOT_AVAILABLE
            note = (
                f"{len(uuids)} candidate advertisements checked; none advertised by "
                f"this entity or its registered subunits "
                f"({rejected} belonged to another entity)"
            )
        elif not consulted:
            # Nothing was consulted for this company. Saying "nothing found" here
            # would assert a search that never happened, so the honest state is
            # that the question could not be resolved this run.
            availability = Availability.FAILED
            note = ("the hiring source was not consulted for this company: no "
                    "candidate index or run snapshot was available and the live "
                    "search budget for this run was exhausted")
        else:
            availability = Availability.NOT_AVAILABLE
            note = ("checked the Nav candidate index and run snapshot; no active "
                    "advertisement is registered to this organisation number or "
                    "its registered subunits")

        claims = [Claim(
            field="job_postings", family="hiring", value=jobs or None,
            availability=availability, evidence_ids=[e.id for e in evidence],
            note=note, as_of=evidence[0].retrieved_at if evidence else None)]

        if jobs:
            dates = sorted(j["published"] for j in jobs if j.get("published"))
            claims.append(Claim(
                field="hiring_signal", family="hiring",
                value={"open_advertisements": len(jobs),
                       "most_recent_published": dates[-1] if dates else None,
                       "earliest_observed": dates[0] if dates else None},
                availability=Availability.AVAILABLE,
                evidence_ids=[e.id for e in evidence][:3],
                as_of=evidence[0].retrieved_at))
        else:
            claims.append(Claim(
                field="hiring_signal", family="hiring", value=None,
                availability=availability,
                note="no verified advertisement, so no hiring direction is asserted"))
        return claims, evidence


def _match_key(name: str | None) -> str:
    """Normalised employer name used to join advertisements to companies.

    Branch markers are stripped so "JOBZONE NORGE AS AVD BODØ" and "JOBZONE NORGE
    AS" collide; the organisation-number check on the advertisement page is what
    decides whether that collision is a real match.
    """
    cleaned = re.sub(r"\s+AVD\s+.*$", "", (name or "").strip(), flags=re.I)
    key = normalise_name(cleaned)
    return key if len(key) >= 3 else ""


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def _first_clean(pattern: re.Pattern[str], text: str) -> str | None:
    """First match that is real content rather than an empty-state UI string."""
    for m in pattern.finditer(text):
        value = " ".join(m.group(1).split())
        if len(value) >= 2 and not any(n in value.lower() for n in _UI_NOISE):
            return value[:160]
    return None


def fetch_public_feed_token(fetcher: Fetcher) -> str | None:
    """Nav publishes a rotating public token for its official change feed."""
    resp = fetcher.fetch(FEED_TOKEN_URL, accept="text/plain")
    if not resp.ok:
        return None
    m = re.search(r"(eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)", resp.text)
    return m.group(1) if m else None
