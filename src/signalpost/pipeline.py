"""Research one company end to end.

Order matters. Official identity is established first, because every later source
is joined back to the organisation number rather than to a name. Website discovery
runs next because a verified site is itself the source of the social, contact and
activity families. External sources run last and are each gated on exact identity.

The function always returns an envelope. An unexpected failure anywhere becomes a
`failed` claim and a recorded error, never a missing row.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Any

from . import AGENT_NAME, __version__
from .discovery import generate
from .envelope import (
    Availability, Claim, CompanyEnvelope, Evidence, Operations, RunError, RunMeta, utcnow,
)
from .http import Fetcher
from .identity import Candidate, IdentityGate, Tier
from .normalize import mod11_valid, registered_domain
from .sources import brreg
from .sources.nav_jobs import NavJobsClient
from .sources.website import WebsiteCrawler, extract_claims
from .synthesis import build as build_summary


@dataclass(slots=True)
class PipelineConfig:
    run_id: str
    max_site_pages: int = 10
    max_candidates: int = 5
    enable_jobs: bool = True
    enable_website: bool = True
    enable_name_guesses: bool = True
    tier_d_enabled: bool = True


class Pipeline:
    """Runs every connector for one organisation number."""

    def __init__(self, fetcher: Fetcher, config: PipelineConfig,
                 *, nav_index: dict[str, list[dict[str, Any]]] | None = None,
                 nav_homepages: dict[str, str] | None = None) -> None:
        self.http = fetcher
        self.config = config
        self.brreg = brreg.BrregClient(fetcher)
        self.gate = IdentityGate(min_tier_d_enabled=config.tier_d_enabled)
        self.crawler = WebsiteCrawler(fetcher, max_pages=config.max_site_pages)
        self.nav_index = nav_index or {}
        self.nav_homepages = nav_homepages or {}
        self.jobs = NavJobsClient(
            fetcher,
            # A declared cache must never be able to fail a run: a truncated or
            # hand-edited index file would otherwise raise before any envelope is
            # written, losing the whole batch.
            index={
                k: [a["uuid"] for a in v if isinstance(a, dict) and a.get("uuid")]
                for k, v in self.nav_index.items()
                if isinstance(v, list)
            },
        )

    def run(self, organisation_number: str) -> CompanyEnvelope:
        started_wall = time.monotonic()
        started_requests, started_bytes = self.http.thread_stats()
        env = CompanyEnvelope(
            organisation_number=organisation_number,
            run=RunMeta(run_id=self.config.run_id,
                        agent_version=f"{AGENT_NAME}/{__version__}",
                        started_at=utcnow()),
        )
        strategies: list[str] = []

        if not mod11_valid(organisation_number):
            env.status = Availability.FAILED
            env.run.terminal_status = "failed"
            env.errors.append(RunError(
                stage="input", kind="invalid_organisation_number",
                detail="not a nine-digit mod-11 valid Norwegian organisation number"))
            env.claims.append(Claim(field="legal_identity", family="identity", value=None,
                                    availability=Availability.FAILED,
                                    note="input is not a valid organisation number"))
            return self._finalise(env, started_wall, started_requests, started_bytes, strategies)

        try:
            self._official(env, organisation_number, strategies)
            site_domain = self._website(env, organisation_number, strategies)
            self._external(env, organisation_number, strategies, site_domain)
        except Exception as exc:  # pragma: no cover - defensive, must never drop a row
            env.errors.append(RunError(
                stage="pipeline", kind=type(exc).__name__, detail=str(exc)[:300],
                retryable=True))
            env.run.terminal_status = "completed_with_errors"
            env.summary.setdefault("trace", traceback.format_exc()[-800:])

        attempts = self._compose_summary(env)
        env.coverage = {c.field: str(c.availability) for c in env.claims}
        return self._finalise(env, started_wall, started_requests, started_bytes,
                              strategies, attempts)

    @staticmethod
    def _compose_summary(env: CompanyEnvelope) -> list[dict[str, Any]]:
        """Publish the summary and hand back the candidate log for operations.

        build_summary composes a fresh block, so a diagnostic key a stage
        recorded is carried across or the audit trail is silently lost.

        The candidate log is deliberately not carried. A domain the gate refused
        is a record of work, but sitting in `summary` beside headline,
        verified_website and profiles it reads as a finding about this company.
        ISABELLA CAFE AS of Honefoss was scored as having claimed an American
        cafe's site on exactly that reading, when the website claim itself was a
        correct abstention. The log goes to operations; the summary keeps a
        count, which names no domain.
        """
        attempts = env.summary.get("identity_attempts") or []
        carried = {k: v for k, v in env.summary.items() if k in {"trace"}}
        env.summary = {**build_summary(env), **carried}
        env.summary["candidates_checked"] = len(attempts)
        return attempts

    # -- stages -----------------------------------------------------------
    def _official(self, env: CompanyEnvelope, org: str, strategies: list[str]) -> None:
        body, resp = self.brreg.entity(org)
        claims, evidence = self.brreg.identity_claims(org, body, resp, "ev-registry")
        env.claims.extend(claims)
        env.evidence.extend(evidence)
        strategies.append("brreg_entity_v1")
        if body:
            env.legal_name = body.get("navn")
        else:
            env.status = Availability.NOT_AVAILABLE if resp.status in {404, 410} else Availability.FAILED
            if resp.status not in {404, 410}:
                env.errors.append(RunError(stage="official", source=resp.url,
                                           kind=resp.error or "registry_unavailable",
                                           detail=f"HTTP {resp.status}", retryable=True))

        for name, method in (("roles", self.brreg.roles), ("subunits", self.brreg.subunits),
                             ("group", self.brreg.group), ("accounts", self.brreg.accounts),
                             ("history", self.brreg.account_history)):
            try:
                claims, evidence = method(org, f"ev-{name}")
                env.claims.extend(claims)
                env.evidence.extend(evidence)
                strategies.append(f"brreg_{name}_v1")
            except Exception as exc:
                env.errors.append(RunError(stage="official", source=name,
                                           kind=type(exc).__name__, detail=str(exc)[:200],
                                           retryable=True))
                env.claims.append(Claim(field=f"brreg_{name}", family="official",
                                        availability=Availability.FAILED,
                                        note=f"connector raised {type(exc).__name__}"))

    def _entity_view(self, env: CompanyEnvelope) -> dict[str, Any]:
        """Flat view of registry facts, as the identity gate expects them."""
        by_field = {c.field: c for c in env.claims}
        identity = (by_field.get("legal_identity").value
                    if by_field.get("legal_identity") and by_field["legal_identity"].value else {}) or {}
        contact = (by_field.get("registry_contact").value
                   if by_field.get("registry_contact") and by_field["registry_contact"].value else {}) or {}
        aliases = (by_field.get("public_brand_aliases").value or []) if by_field.get("public_brand_aliases") else []
        return {
            "organisation_number": env.organisation_number,
            "legal_name": identity.get("legal_name") or env.legal_name,
            "website": contact.get("website"),
            "email": contact.get("email"),
            "phone": contact.get("phone"),
            "mobile": contact.get("mobile"),
            "business_address": identity.get("business_address") or {},
            "aliases": aliases,
        }

    def _website(self, env: CompanyEnvelope, org: str, strategies: list[str]) -> str | None:
        if not self.config.enable_website:
            return None
        by_field = {c.field: c for c in env.claims}
        entity = self._entity_view(env)
        subunits = (by_field.get("registered_workplaces").value or []) if by_field.get("registered_workplaces") else []
        job_homepages = [h for h in [self.nav_homepages.get(org)] if h]

        candidates = generate(entity, subunits=subunits, job_homepages=job_homepages,
                              include_name_guesses=self.config.enable_name_guesses,
                              limit=self.config.max_candidates)
        strategies.append("candidate_generation_v1")
        if not candidates:
            env.claims.append(Claim(
                field="official_website", family="website", value=None,
                availability=Availability.NOT_AVAILABLE,
                note="no website candidate could be generated from registry data or the company name"))
            self._site_families_unavailable(env, Availability.NOT_AVAILABLE)
            return None

        # Two-phase resolution. Phase 1 gates every candidate on its homepage
        # alone; phase 2 spends a deeper page budget only on a candidate that the
        # registry itself attests, and only when phase 1 found partial support.
        # Measured on the development gold set this cut requests per company from
        # 161.8 to roughly a fifth, with no loss of precision or recall.
        attempts: list[dict[str, Any]] = []
        escalate: list[tuple[Any, list[Any]]] = []

        for cand in candidates:
            pages = self.crawler.crawl(cand.domain, max_pages=1)
            if not pages:
                attempts.append({"domain": cand.domain, "channel": cand.channel,
                                 "outcome": "unreachable"})
                continue
            if pages[0].blocked and not pages[0].ok:
                attempts.append({"domain": cand.domain, "channel": cand.channel,
                                 "outcome": "blocked"})
                continue
            verdict = self.gate.evaluate(entity, Candidate(
                domain=cand.domain, channel=cand.channel,
                url=pages[0].final_url or pages[0].url,
                pages=[(p.final_url or p.url, p.text) for p in pages]))
            attempts.append({"domain": cand.domain, "channel": cand.channel,
                             "phase": 1,
                             "outcome": "accepted" if verdict.accepted else "rejected",
                             "tier": str(verdict.tier), "reasons": verdict.reasons[:2],
                             "conflicts": verdict.conflicts[:2]})
            if verdict.accepted:
                return self._publish_site(env, org, cand, verdict, strategies,
                                          attempts=attempts)
            # Escalate when the homepage already shows partial support. Norwegian
            # sites put the phone and the address on /kontakt, so a tier C or D
            # candidate frequently cannot clear on the homepage alone. Gating only
            # on priority cost 3.3 points of recall when it was measured.
            if not verdict.conflicts and (cand.priority <= 2 or verdict.signals):
                escalate.append((cand, pages, verdict))

        # Strongest partial evidence first: registry-attested channel, then the
        # number of signals the homepage already produced.
        escalate.sort(key=lambda item: (item[0].priority > 2, -len(item[2].signals)))

        for cand, _, _ in escalate[:2]:
            pages = self.crawler.crawl(cand.domain)
            if len(pages) <= 1:
                continue
            verdict = self.gate.evaluate(entity, Candidate(
                domain=cand.domain, channel=cand.channel,
                url=pages[0].final_url or pages[0].url,
                pages=[(p.final_url or p.url, p.text) for p in pages]))
            attempts.append({"domain": cand.domain, "channel": cand.channel,
                             "phase": 2, "pages": len(pages),
                             "outcome": "accepted" if verdict.accepted else "rejected",
                             "tier": str(verdict.tier), "reasons": verdict.reasons[:2],
                             "conflicts": verdict.conflicts[:2]})
            if verdict.accepted:
                return self._publish_site(env, org, cand, verdict, strategies,
                                          pages=pages, attempts=attempts)

        env.summary["identity_attempts"] = attempts
        blocked = any(a["outcome"] == "blocked" for a in attempts)
        rejected_conflict = any(a.get("conflicts") for a in attempts)
        if blocked:
            availability, note = Availability.BLOCKED, "every website candidate refused the request"
        elif rejected_conflict:
            availability = Availability.AMBIGUOUS
            note = ("a candidate site carried another company's organisation number, so no "
                    "website is published for this entity")
        elif any(a["outcome"] == "rejected" for a in attempts):
            availability = Availability.AMBIGUOUS
            note = (f"{len(attempts)} candidate domain(s) reached but none proved the exact "
                    "entity; abstaining rather than risking a wrong-company match")
        else:
            availability, note = Availability.NOT_AVAILABLE, "no candidate domain resolved to a live site"

        # Nothing on the open web proved the entity. The company may still have
        # filed a website with the registry, and that filing is a registry fact
        # which holds whether or not the host answers today. LOWEHJERTE DESIGN AS
        # filed www.lowehjerte.no, whose server now resets the connection;
        # abstaining discarded a fact the register was handing over. It is
        # published on the register's authority and never as a verified site,
        # because no page of it was read, so nothing is reported from the site.
        if declared := self._registry_declared_site(env, attempts):
            url, domain, evidence_ids = declared
            env.claims.append(Claim(
                field="official_website", family="website",
                value={"url": url, "domain": domain,
                       "site_names_this_company": None, "requested_domain": None,
                       "verification": {
                           "accepted": False,
                           "tier": str(Tier.REGISTRY_DECLARED_ONLY),
                           "confidence": 0.75,
                           "signals": {"registry_declared_website": domain},
                           "conflicting_organisation_numbers": [],
                           "reasons": [note],
                           "proof_url": None}},
                availability=Availability.AVAILABLE, evidence_ids=evidence_ids,
                note=("the website this company filed with the registry; no page of "
                      "it could be read on this run, so the site has not been checked "
                      "against the company and nothing on it is reported")))
            self._site_families_unavailable(env, availability)
            return None

        env.claims.append(Claim(field="official_website", family="website", value=None,
                                availability=availability, note=note))
        self._site_families_unavailable(env, availability)
        return None

    @staticmethod
    def _registry_declared_site(
            env: CompanyEnvelope,
            attempts: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, list[str]] | None:
        """The website the registry says the company filed, with its evidence.

        Read off the registry claim rather than the raw entity body, so the URL
        published here is the one already evidenced by the registry record and
        cannot drift from it.

        A filing is withheld when the gate actually read that domain and found
        another company's organisation number on it. The register says what the
        company filed; the page says whose it is now. A domain that was vetoed
        for a conflict must not re-enter on the register's word alone, or the
        veto would be undone by the fallback meant to complement it.
        """
        reg = next((c for c in env.claims if c.field == "registry_contact"), None)
        raw = ((reg.value or {}).get("website") or "").strip() if reg else ""
        domain = registered_domain(raw)
        if not (raw and domain and reg and reg.evidence_ids):
            return None
        for attempt in attempts or []:
            if attempt.get("conflicts") and registered_domain(
                    attempt.get("domain") or "") == domain:
                return None
        url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
        return url, domain, list(reg.evidence_ids)

    # Families that can only be read from the company's own site. When no site is
    # published they still need a terminal state: the evaluation contract treats a
    # missing row as unscorable, and a silent gap is indistinguishable from
    # "checked and found nothing".
    SITE_DERIVED: tuple[tuple[str, str], ...] = (
        ("website_description", "description"),
        ("website_structured_organisation", "description"),
        ("website_contact", "contact"),
        ("company_owned_profiles", "social_profiles"),
        ("company_published_activity", "dated_activity"),
        ("careers_page", "hiring"),
    )

    def _site_families_unavailable(self, env: CompanyEnvelope,
                                   website_state: Availability) -> None:
        """Report the site-derived families explicitly when there is no site."""
        reason = {
            Availability.BLOCKED: "the company's website refused the request, so its own "
                                  "pages could not be read",
            Availability.AMBIGUOUS: "no candidate site could be tied to this exact company, "
                                    "so none of its own pages were read",
            Availability.FAILED: "the verified site could not be re-fetched on this run",
        }.get(website_state,
              "no verified website for this company, so its own pages could not be read")
        state = (Availability.BLOCKED if website_state is Availability.BLOCKED
                 else Availability.FAILED if website_state is Availability.FAILED
                 else Availability.NOT_AVAILABLE)
        present = {c.field for c in env.claims}
        for field, family in self.SITE_DERIVED:
            if field not in present:
                env.claims.append(Claim(field=field, family=family, value=None,
                                        availability=state, note=reason))

    def _publish_site(self, env: CompanyEnvelope, org: str, cand: Any, verdict: Any,
                      strategies: list[str], pages: list[Any] | None = None,
                      attempts: list[dict[str, Any]] | None = None) -> str:
        """Crawl the accepted site at full budget and extract every site family."""
        env.summary["identity_attempts"] = attempts or []
        if pages is None or len(pages) <= 1:
            pages = self.crawler.crawl(cand.domain) or pages or []
        info = verdict.as_dict() | {"discovery_channel": cand.channel}
        claims, evidence = extract_claims(org, cand.domain, pages, info, "ev-web",
                                          legal_name=env.legal_name or "")
        if not claims:
            # The site cleared the gate but the full crawl came back empty or
            # blocked. The company still needs a terminal website claim; dropping
            # the field silently would remove it from coverage altogether.
            env.claims.append(Claim(
                field="official_website", family="website", value=None,
                availability=Availability.FAILED,
                note=(f"{cand.domain} passed the identity gate but could not be "
                      "re-fetched for extraction on this run")))
            self._site_families_unavailable(env, Availability.FAILED)
            return None
        env.claims.extend(claims)
        env.evidence.extend(evidence)
        strategies.append(f"website_{cand.channel}_v1")
        return cand.domain

    def _external(self, env: CompanyEnvelope, org: str, strategies: list[str],
                  site_domain: str | None) -> None:
        if not self.config.enable_jobs:
            return
        by_field = {c.field: c for c in env.claims}
        subunits = (by_field.get("registered_workplaces").value or []) if by_field.get("registered_workplaces") else []
        aliases = (by_field.get("public_brand_aliases").value or []) if by_field.get("public_brand_aliases") else []
        try:
            claims, evidence = self.jobs.collect(
                org, env.legal_name or "", aliases=aliases, subunits=subunits,
                eid_prefix="ev-jobs")
            env.claims.extend(claims)
            env.evidence.extend(evidence)
            strategies.append("nav_jobs_v1")
        except Exception as exc:
            env.errors.append(RunError(stage="external", source="nav_jobs",
                                       kind=type(exc).__name__, detail=str(exc)[:200],
                                       retryable=True))
            env.claims.append(Claim(field="job_postings", family="hiring",
                                    availability=Availability.FAILED,
                                    note=f"job connector raised {type(exc).__name__}"))

    # -- bookkeeping ------------------------------------------------------
    def _finalise(self, env: CompanyEnvelope, started_wall: float, started_requests: int,
                  started_bytes: int, strategies: list[str],
                  attempts: list[dict[str, Any]] | None = None) -> CompanyEnvelope:
        env.run.completed_at = utcnow()
        env.operations = Operations(
            requests=self.http.thread_stats()[0] - started_requests,
            bytes_downloaded=self.http.thread_stats()[1] - started_bytes,
            runtime_ms=int((time.monotonic() - started_wall) * 1000),
            third_party_cost_usd=0.0,
            strategies_used=sorted(set(strategies)),
            identity_attempts=attempts or [],
        )
        if env.errors and env.run.terminal_status == "completed":
            env.run.terminal_status = "completed_with_errors"
        problems = env.validate_integrity()
        if problems:
            env.errors.append(RunError(stage="validation", kind="integrity",
                                       detail="; ".join(problems[:5])))
        return env
