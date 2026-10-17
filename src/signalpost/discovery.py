"""Website candidate generation.

Candidates are ranked by how strongly the registry itself attests them, because
measured hit rates differ by an order of magnitude between channels:

  registry `hjemmeside`      declared by the company, 10.91% of the universe
  registry e-mail domain     declared by the company, +9.56pp of the universe
  subunit website            declared on a registered workplace
  name-derived guess         4.7% verified hit rate (experiments/exp01)

Search-provider candidates are supported but disabled by default; a search result
is a candidate only and never the evidence for a published fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .normalize import (
    domain_candidates_from_name,
    email_domain,
    is_platform_host,
    registered_domain,
)

CHANNEL_PRIORITY: dict[str, int] = {
    "registry_website": 0,
    "registry_email_domain": 1,
    "subunit_website": 2,
    "job_posting_homepage": 3,
    "group_parent_website": 4,
    "name_guess": 5,
    "search_provider": 6,
}


@dataclass(slots=True, frozen=True)
class DomainCandidate:
    domain: str
    channel: str
    raw: str = ""

    @property
    def priority(self) -> int:
        return CHANNEL_PRIORITY.get(self.channel, 99)


def _clean(value: str | None) -> str:
    domain = registered_domain(value or "")
    if not domain or is_platform_host(domain):
        return ""
    # A bare TLD or an obviously malformed host is not a candidate.
    return domain if "." in domain and len(domain) >= 4 else ""


def generate(
    entity: dict[str, Any],
    *,
    subunits: Iterable[dict[str, Any]] = (),
    job_homepages: Iterable[str] = (),
    parent_website: str | None = None,
    include_name_guesses: bool = True,
    limit: int = 8,
) -> list[DomainCandidate]:
    """Ordered, de-duplicated domain candidates for one entity."""
    found: dict[str, DomainCandidate] = {}

    def add(raw: str | None, channel: str) -> None:
        domain = _clean(raw)
        if not domain:
            return
        existing = found.get(domain)
        cand = DomainCandidate(domain=domain, channel=channel, raw=raw or "")
        if existing is None or cand.priority < existing.priority:
            found[domain] = cand

    add(entity.get("website") or entity.get("hjemmeside"), "registry_website")
    add(email_domain(entity.get("email") or entity.get("epostadresse")), "registry_email_domain")
    for sub in subunits:
        add(sub.get("website"), "subunit_website")
    for home in job_homepages:
        add(home, "job_posting_homepage")
    add(parent_website, "group_parent_website")

    if include_name_guesses:
        for guess in domain_candidates_from_name(
            entity.get("legal_name") or entity.get("navn") or "", limit=3
        ):
            add(guess, "name_guess")
        # Former registered names are registry-attested brand aliases.
        for alias in (entity.get("aliases") or [])[:2]:
            for guess in domain_candidates_from_name(alias)[:2]:
                add(guess, "name_guess")

    return sorted(found.values(), key=lambda c: (c.priority, c.domain))[:limit]
