"""Verified company website - crawl, extract and publish.

Static HTML first. Structured data (JSON-LD, microdata, OpenGraph) is read before
heuristics, readable text after that, and a model is never used to decide identity
or to invent a field.

Only a site that has passed the identity gate reaches this module, so everything
extracted here is already bound to the exact legal entity.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

from ..envelope import Availability, Claim, Evidence, SourceClass
from ..http import Fetcher, Response
from ..normalize import is_platform_host, normalise_text, registered_domain

# Paths worth trying on a Norwegian company site, Bokmål and Nynorsk included.
PRIORITY_PATHS: tuple[str, ...] = (
    "/om-oss", "/om", "/about", "/about-us", "/kontakt", "/kontakt-oss", "/contact",
    "/ledelse", "/team", "/ansatte", "/leadership", "/management", "/styret",
    "/karriere", "/jobb", "/jobber", "/ledige-stillinger", "/careers", "/jobs",
    "/nyheter", "/aktuelt", "/nytt", "/news", "/blogg", "/blog", "/presse", "/press",
    "/lokasjoner", "/avdelinger", "/locations", "/investor", "/personvern",
)
LINK_HINTS = re.compile(
    r"(om\s*oss|about|kontakt|contact|ledelse|team|ansatte|styret|karriere|jobb|"
    r"ledige\s*stillinger|career|nyheter|aktuelt|news|presse|press|avdeling|lokasjon)",
    re.I)

SOCIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "linkedin": re.compile(r"https?://(?:[\w-]+\.)?linkedin\.com/(?:company|school)/[\w%.\-]+", re.I),
    "facebook": re.compile(r"https?://(?:[\w-]+\.)?facebook\.com/(?!sharer|dialog|plugins)[\w%.\-]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|reel/)[\w%.\-]+", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:@[\w%.\-]+|channel/[\w\-]+|c/[\w%.\-]+|user/[\w%.\-]+)", re.I),
    "x": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(?!intent|share|home)[\w%.\-]+", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w%.\-]+", re.I),
}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
# A bare eight-digit run is not a phone number: order references, amounts and
# dates all match that shape and were being published as contact details. Accept
# an international prefix, an explicitly grouped number, or digits introduced by
# a phone label.
PHONE_RE = re.compile(
    r"(?:\+\s?47[\s.-]?\d{2}[\s.-]?\d{2}[\s.-]?\d{2}[\s.-]?\d{2})"      # +47 ...
    r"|(?:(?<!\d)\d{2}[\s.-]\d{2}[\s.-]\d{2}[\s.-]\d{2}(?!\d))"          # 51 68 57 00
    r"|(?:(?<!\d)\d{3}[\s.-]\d{2}[\s.-]\d{3}(?!\d))"                      # 979 62 230
    r"|(?:(?:tlf|telefon|mobil|tel|phone|ring)\.?:?\s*(?<!\d)\d{8}(?!\d))",  # labelled
    re.I)
DATE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})|(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})|"
    r"(\d{1,2}\.?\s+(?:januar|februar|mars|april|mai|juni|juli|august|september|"
    r"oktober|november|desember)\s+\d{4})", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]{1,400})["']""", re.I)
ANCHOR_RE = re.compile(r"""<a\b[^>]*href\s*=\s*["']([^"'#]{1,400})["'][^>]*>(.{0,160}?)</a>""",
                       re.S | re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
DESC_RE = re.compile(
    r"""<meta[^>]+(?:name|property)\s*=\s*["'](?:description|og:description)["'][^>]*"""
    r"""content\s*=\s*["']([^"']{10,400})["']""", re.I)

NO_MONTHS = {m: i for i, m in enumerate(
    ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august",
     "september", "oktober", "november", "desember"], start=1)}


def _evidence(eid: str, resp: Response, *, span: str | None = None,
              method: str = "static_html") -> Evidence:
    return Evidence(
        id=eid, source_url=resp.url, source_class=SourceClass.COMPANY_OWNED,
        retrieved_at=resp.retrieved_at, content_sha256=resp.content_sha256,
        http_status=resp.status, final_url=resp.final_url or resp.url,
        redirect_chain=resp.redirect_chain, claim_span=span,
        extraction_method=method,
        access_policy="company-controlled site, robots.txt honoured",
    )


class WebsiteCrawler:
    """Fetches a bounded set of pages from one verified company site."""

    def __init__(self, fetcher: Fetcher, *, max_pages: int = 10) -> None:
        self.http = fetcher
        self.max_pages = max_pages

    def crawl(self, domain: str, max_pages: int | None = None) -> list[Response]:
        """Homepage plus the highest-value internal pages, within a page budget.

        `max_pages=1` fetches only the homepage. Identity gating runs at that
        budget first, so pages are never spent crawling a candidate that is about
        to be rejected.
        """
        budget = self.max_pages if max_pages is None else max_pages
        # Probe in cost order and stop at the first answer. HTTPS on the bare
        # domain answers for the large majority; falling back to plain HTTP is
        # worth a request only when TLS or the connection itself failed, not when
        # the server replied with a status.
        home: Response | None = None
        last: Response | None = None
        for host in (domain, f"www.{domain}"):
            if not self.http.resolves(host):
                continue
            resp = self.http.fetch(f"https://{host}/")
            last = resp
            if resp.ok and resp.text:
                home = resp
                break
            if resp.blocked:
                return [resp]
            if resp.status is None and resp.error in {"tls_error", "connection_refused"}:
                resp = self.http.fetch(f"http://{host}/")
                last = resp
                if resp.ok and resp.text:
                    home = resp
                    break
                if resp.blocked:
                    return [resp]
        if home is None:
            return [last] if last is not None and last.blocked else []

        pages = [home]
        if budget <= 1:
            return pages
        base = home.final_url or home.url
        origin = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
        seen = {_canonical(base)}
        queue: list[str] = []

        for match in ANCHOR_RE.finditer(home.text):
            href, label = match.group(1).strip(), normalise_text(match.group(2))
            if not LINK_HINTS.search(label) and not LINK_HINTS.search(href):
                continue
            target = urljoin(base, href)
            if urlsplit(target).netloc != urlsplit(base).netloc:
                continue
            key = _canonical(target)
            if key not in seen:
                seen.add(key)
                queue.append(target)
        for path in PRIORITY_PATHS:
            target = origin + path
            key = _canonical(target)
            if key not in seen:
                seen.add(key)
                queue.append(target)

        for url in queue[: budget - 1]:
            resp = self.http.fetch(url)
            if resp.ok and resp.text:
                pages.append(resp)
        return pages


def _canonical(url: str) -> str:
    """De-duplication key. The query matters: /nyheter?side=1 and ?side=2 differ."""
    parts = urlsplit(url)
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.netloc.lower()}{parts.path.rstrip('/').lower()}{query}"


def _structured(html: str, url: str) -> list[dict[str, Any]]:
    """JSON-LD / microdata / OpenGraph, tolerating a parser failure."""
    try:
        import extruct  # noqa: PLC0415

        data = extruct.extract(html, base_url=url,
                               syntaxes=["json-ld", "microdata", "opengraph"], uniform=True)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for syntax in ("json-ld", "microdata", "opengraph"):
        for item in data.get(syntax, []) or []:
            if isinstance(item, dict):
                out.append(item)
    return out


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw):
        return raw
    if m := re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", raw):
        d, mo, y = (int(x) for x in m.groups())
        return _safe_iso(y, mo, d)
    if m := re.fullmatch(r"(\d{1,2})\.?\s+([a-zæøå]+)\s+(\d{4})", raw, re.I):
        d, name, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        if name in NO_MONTHS:
            return _safe_iso(y, NO_MONTHS[name], d)
    return None


def _safe_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_claims(org: str, domain: str, pages: list[Response],
                   verdict_info: dict[str, Any], eid_prefix: str) -> tuple[list[Claim], list[Evidence]]:
    """Turn a crawled, identity-verified site into claims across several families."""
    claims: list[Claim] = []
    evidence: list[Evidence] = []
    if not pages:
        return claims, evidence

    home = pages[0]
    if not home.ok or not home.text:
        # The full-budget re-crawl came back blocked or empty. Publishing it would
        # assert a website from a 403 page with no body behind it.
        return claims, evidence
    ev_home = f"{eid_prefix}-home"
    evidence.append(_evidence(ev_home, home, span=_title_of(home.text)))

    # 1. the verified website itself
    claims.append(Claim(
        field="official_website", family="website",
        value={"url": home.final_url or home.url, "domain": domain,
               "verification": verdict_info},
        availability=Availability.AVAILABLE, confidence=verdict_info.get("confidence", 0.9),
        evidence_ids=[ev_home], as_of=home.retrieved_at))

    # 2. self-described business description
    title = _title_of(home.text)
    desc = None
    if m := DESC_RE.search(home.text):
        desc = " ".join(m.group(1).split())
    claims.append(Claim(
        field="website_description", family="description",
        value={"title": title, "description": desc} if (title or desc) else None,
        availability=Availability.AVAILABLE if (title or desc) else Availability.NOT_AVAILABLE,
        evidence_ids=[ev_home], as_of=home.retrieved_at,
        note=None if (title or desc) else "no title or meta description on the homepage"))

    # 3. company-owned profiles declared by the verified site
    profiles: dict[str, str] = {}
    profile_src: dict[str, str] = {}
    for page in pages:
        for platform, pattern in SOCIAL_PATTERNS.items():
            if platform in profiles:
                continue
            if m := pattern.search(page.text):
                url = m.group(0).rstrip("/\"'")
                if not _is_generic_profile(url):
                    profiles[platform] = url
                    profile_src[platform] = page.final_url or page.url
    ev_profiles: list[str] = []
    if profiles:
        src_pages = {profile_src[p] for p in profiles}
        for i, page in enumerate(p for p in pages if (p.final_url or p.url) in src_pages):
            eid = f"{eid_prefix}-social-{i}"
            evidence.append(_evidence(eid, page, method="outbound_link_extraction"))
            ev_profiles.append(eid)
    claims.append(Claim(
        field="company_owned_profiles", family="social_profiles",
        value=[{"platform": k, "url": v, "declared_on": profile_src[k]}
               for k, v in sorted(profiles.items())] or None,
        availability=Availability.AVAILABLE if profiles else Availability.NOT_AVAILABLE,
        evidence_ids=ev_profiles or [ev_home], as_of=home.retrieved_at,
        note=None if profiles else "verified site links to no company profile"))

    # 4. contact points published by the company
    emails: set[str] = set()
    phones: set[str] = set()
    contact_src: Response | None = None
    for page in pages:
        text = normalise_text(page.text)
        page_emails = {e.lower() for e in EMAIL_RE.findall(page.text)
                       if not e.lower().endswith((".png", ".jpg", ".gif", ".webp"))
                       and "example." not in e.lower() and "@sentry" not in e.lower()}
        page_phones = {_clean_phone(p) for p in PHONE_RE.findall(text)}
        page_phones.discard("")
        if page_emails or page_phones:
            contact_src = contact_src or page
        emails |= page_emails
        phones |= page_phones
    ev_contact = ev_home
    if contact_src is not None and contact_src is not home:
        ev_contact = f"{eid_prefix}-contact"
        evidence.append(_evidence(ev_contact, contact_src, method="regex_contact_block"))
    contact_value = {"emails": sorted(emails)[:5], "phones": sorted(phones)[:5]} if (emails or phones) else None
    claims.append(Claim(
        field="website_contact", family="contact", value=contact_value,
        availability=Availability.AVAILABLE if contact_value else Availability.NOT_AVAILABLE,
        evidence_ids=[ev_contact], as_of=home.retrieved_at,
        note=None if contact_value else "no contact details published on the crawled pages"))

    # 5. structured organisation data the company publishes about itself
    org_data: list[dict[str, Any]] = []
    struct_src: Response | None = None
    for page in pages:
        for item in _structured(page.text, page.final_url or page.url):
            t = str(item.get("@type") or item.get("type") or "")
            if any(k in t for k in ("Organization", "LocalBusiness", "Corporation", "Store")):
                org_data.append({k: v for k, v in item.items() if k in
                                 ("name", "legalName", "address", "telephone", "email",
                                  "foundingDate", "numberOfEmployees", "sameAs", "url", "@type")})
                struct_src = struct_src or page
    ev_struct = ev_home
    if struct_src is not None and struct_src is not home:
        ev_struct = f"{eid_prefix}-structured"
        evidence.append(_evidence(ev_struct, struct_src, method="extruct_structured_data"))
    claims.append(Claim(
        field="website_structured_organisation", family="description",
        value=org_data[:3] or None,
        availability=Availability.AVAILABLE if org_data else Availability.NOT_AVAILABLE,
        evidence_ids=[ev_struct], as_of=home.retrieved_at,
        note=None if org_data else "site embeds no Organization structured data"))

    # 6. dated public activity published by the company
    activity: list[dict[str, Any]] = []
    activity_src: Response | None = None
    for page in pages:
        url = page.final_url or page.url
        if not re.search(r"(nyhet|aktuelt|nytt|news|blogg|blog|presse|press)", url, re.I):
            continue
        for match in ANCHOR_RE.finditer(page.text):
            href, label = match.group(1).strip(), " ".join(normalise_text(match.group(2)).split())
            if len(label) < 12:
                continue
            target = urljoin(url, href)
            if urlsplit(target).netloc != urlsplit(url).netloc:
                continue
            window = page.text[max(0, match.start() - 300): match.end() + 300]
            if dm := DATE_RE.search(normalise_text(window)):
                published = _parse_date(dm.group(0))
                if published:
                    activity.append({"title": label[:160], "url": target, "published": published})
        if activity:
            activity_src = activity_src or page
            break
    activity = _dedupe_activity(activity)[:20]
    ev_activity = ev_home
    if activity_src is not None:
        ev_activity = f"{eid_prefix}-activity"
        evidence.append(_evidence(ev_activity, activity_src, method="dated_anchor_extraction"))
    claims.append(Claim(
        field="company_published_activity", family="dated_activity",
        value=activity or None,
        availability=Availability.AVAILABLE if activity else Availability.NOT_AVAILABLE,
        evidence_ids=[ev_activity], as_of=home.retrieved_at,
        note=None if activity else "no dated news or blog items found on the crawled pages"))

    # 7. careers surface, which is a hiring signal independent of a job board
    careers = [p.final_url or p.url for p in pages
               if re.search(r"(karriere|jobb|ledige-stillinger|career|jobs)", p.final_url or p.url, re.I)]
    claims.append(Claim(
        field="careers_page", family="hiring", value=careers[:3] or None,
        availability=Availability.AVAILABLE if careers else Availability.NOT_AVAILABLE,
        evidence_ids=[ev_home], as_of=home.retrieved_at,
        note=None if careers else "no careers page found on the verified site"))
    return claims, evidence


def _clean_phone(raw: str) -> str:
    """Drop any leading label ("tlf", "mobil") and collapse whitespace."""
    trimmed = re.sub(r"^[^\d+]*", "", raw)
    return " ".join(trimmed.split())


def _title_of(html: str) -> str | None:
    if m := TITLE_RE.search(html):
        return " ".join(normalise_text(m.group(1)).split())[:200] or None
    return None


def _is_generic_profile(url: str) -> bool:
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    return tail in {"", "home", "pages", "profile", "sharer", "share", "login", "privacy"}


def _dedupe_activity(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = item["url"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return sorted(out, key=lambda i: i["published"], reverse=True)
