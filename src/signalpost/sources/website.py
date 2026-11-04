"""Verified company website - crawl, extract and publish.

Static HTML first. Structured data (JSON-LD, microdata, OpenGraph) is read before
heuristics, readable text after that, and a model is never used to decide identity
or to invent a field.

Only a site that has passed the identity gate reaches this module, so everything
extracted here is already bound to the exact legal entity.
"""
from __future__ import annotations

import html as _html
import re
from datetime import date
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

from ..envelope import Availability, Claim, Evidence, SourceClass
from ..http import Fetcher, Response
from ..identity import site_identity_strings as site_identity_of
from ..normalize import (
    is_platform_host, name_tokens, normalise_text, registered_domain, strip_accents,
)

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
NEWS_SEGMENTS: tuple[str, ...] = ("nyheter", "nyhet", "aktuelt", "nytt", "news",
                                  "blogg", "blog", "presse", "press", "artikler")
CAREER_SEGMENTS: tuple[str, ...] = ("karriere", "jobb", "jobber", "stillinger",
                                    "ledige", "career", "careers", "jobs", "vacancies")
# Navigation chrome that is not a news item.
MENU_WORDS = re.compile(r"^(les mer|read more|se alle|view all|neste|forrige|meny|"
                        r"kontakt oss|om oss|tilbake|hjem|home|more|all news|alle nyheter)$", re.I)


def _looks_like_menu(label: str) -> bool:
    return bool(MENU_WORDS.match(label.strip()))


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


def _decode(text: str) -> str:
    """Resolve HTML entities so a title reads 'halvårsrapport', not 'halv&#xe5;rsrapport'."""
    return " ".join(_html.unescape(text or "").split())


def _path_has(url: str, words: tuple[str, ...]) -> bool:
    """Match whole path segments, never substrings.

    'ejobb.no/om-oss' contains the letters 'jobb' and was being published as a
    careers page. Segment matching stops that.
    """
    path = urlsplit(url).path.lower()
    segments = [seg for part in path.split("/") for seg in re.split(r"[-_.]", part) if seg]
    return any(w in segments for w in words)


class _PageEvidence:
    """One evidence record per crawled page, created only when something cites it.

    A reader checking a published item needs the page it was actually read from,
    not the homepage. Every item therefore points at its own retrieval, so a
    careers link cites the careers page and a news item cites the index it was
    listed on.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._ids: dict[str, str] = {}
        self.records: list[Evidence] = []

    def cite(self, page: Response, *, method: str, span: str | None = None) -> str:
        url = page.final_url or page.url
        if url not in self._ids:
            eid = f"{self.prefix}-p{len(self._ids)}"
            self._ids[url] = eid
            self.records.append(_evidence(eid, page, span=span, method=method))
        return self._ids[url]


def _handle_belongs_to(url: str, legal_name: str) -> bool:
    """Does this social handle name the company, or the chain it belongs to?

    AKERSHUS DEKK AS sat on a Dekkpartner dealer page whose footer offered
    facebook.com/bdekk and linkedin.com/company/dekk-partner-as. Both name the
    chain. The handle must carry the company's most distinctive name token.
    """
    handle = strip_accents(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]).lower()
    handle = re.sub(r"[^a-z0-9]", "", handle)
    tokens = sorted((strip_accents(t) for t in name_tokens(legal_name)), key=len, reverse=True)
    tokens = [re.sub(r"[^a-z0-9]", "", t) for t in tokens]
    tokens = [t for t in tokens if len(t) >= 4]
    return bool(handle) and bool(tokens) and tokens[0] in handle


def _names_the_company(text: str, legal_name: str, org: str, site_domain: str) -> bool:
    """Does this page identify the company, rather than merely mention a group?

    Every distinctive token of the legal name must be present, or the page must
    carry the organisation number, or the domain itself must spell the name.
    A single shared token is not enough: 'thon.no' contains 'thon' but is the
    group's site, not THON SENTER BERGEN AS.
    """
    blob = normalise_text(text)
    if org and org in re.sub(r"[^\d]", "", blob):
        return True
    tokens = [t for t in name_tokens(legal_name) if len(t) >= 3]
    if tokens and all(t in blob for t in tokens):
        return True
    joined = "".join(name_tokens(legal_name))
    label = (site_domain or "").split(".")[0].replace("-", "")
    return bool(joined) and bool(label) and (joined == label or joined.startswith(label) and len(label) >= 6)


def extract_claims(org: str, domain: str, pages: list[Response],
                   verdict_info: dict[str, Any], eid_prefix: str,
                   legal_name: str = "") -> tuple[list[Claim], list[Evidence]]:
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
    cites = _PageEvidence(eid_prefix)
    ev_home = cites.cite(home, method="static_html", span=_title_of(home.text))
    evidence = cites.records

    # 1. the verified website itself.
    #
    # Publish the domain the crawl actually landed on, not the candidate we
    # started from. fusetools.com redirects to fuse.no, and recording the
    # candidate string next to a final_url of https://fuse.no/ put two
    # contradictory facts inside one claim.
    final_url = home.final_url or home.url
    # The gate decides which domain is this company's: normally the one the crawl
    # landed on, but a registry-declared domain stands even when it forwards.
    final_domain = (verdict_info.get("publish_domain")
                    or registered_domain(final_url) or registered_domain(domain))
    # Does this site belong to the company, or to a group/chain it sits under?
    # When it is the group's, the site itself is still reported, but none of the
    # group's social accounts, news or jobs are published as this company's.
    site_domain = final_domain
    own_pages = [pg for pg in pages
                 if registered_domain(pg.final_url or pg.url) == site_domain]
    identity_text = " ".join(
        (_title_of(pg.text) or "") + " " + " ".join(site_identity_of(pg.text))
        for pg in (own_pages or pages)[:4])
    is_own_site = _names_the_company(identity_text, legal_name, org, site_domain)
    shared_note = (
        "the verified site does not identify this company by name, so it is treated "
        "as a group or chain site; its profiles, news and jobs are not published as "
        "this company's")

    claims.append(Claim(
        field="official_website", family="website",
        value={"url": final_url, "domain": final_domain,
               "site_names_this_company": is_own_site,
               "requested_domain": domain if final_domain != registered_domain(domain) else None,
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

    # 3. company-owned profiles declared by the verified site.
    #
    # Only links found on the company's own pages count. A footer on a chain
    # portal carries the chain's accounts: AKERSHUS DEKK AS resolved through to
    # dekkpartner.no, whose footer offered facebook.com/bdekk.
    profiles: list[dict[str, Any]] = []
    seen_platforms: set[str] = set()
    if is_own_site:
        for page in own_pages:
            page_url = page.final_url or page.url
            for platform, pattern in SOCIAL_PATTERNS.items():
                if platform in seen_platforms:
                    continue
                m = pattern.search(page.text)
                if not m:
                    continue
                url = m.group(0).rstrip("/\"'")
                if _is_generic_profile(url) or not _handle_belongs_to(url, legal_name):
                    continue
                seen_platforms.add(platform)
                profiles.append({
                    "platform": platform, "url": url,
                    "declared_on": page_url,
                    "evidence_id": cites.cite(page, method="outbound_link_extraction",
                                              span=url),
                })
    claims.append(Claim(
        field="company_owned_profiles", family="social_profiles",
        value=profiles or None,
        availability=Availability.AVAILABLE if profiles else Availability.NOT_AVAILABLE,
        evidence_ids=[p["evidence_id"] for p in profiles] or [ev_home],
        as_of=home.retrieved_at,
        note=None if profiles else (shared_note if not is_own_site else
                                    "no profile on the verified site carries this "
                                    "company's name; chain and agency accounts are "
                                    "not published as its own")))

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

    # 6. dated public activity published by the company.
    #
    # An item counts only when it is a dated post on the company's own site and
    # its link target is a real article, not a menu label or a product page.
    activity: list[dict[str, Any]] = []
    if is_own_site:
        for page in own_pages:
            url = page.final_url or page.url
            if not _path_has(url, NEWS_SEGMENTS):
                continue
            for match in ANCHOR_RE.finditer(page.text):
                href = match.group(1).strip()
                label = _decode(normalise_text(match.group(2)))
                if len(label) < 15 or _looks_like_menu(label):
                    continue
                target = urljoin(url, href)
                if registered_domain(target) != site_domain:
                    continue
                if urlsplit(target).path.rstrip("/") in ("", urlsplit(url).path.rstrip("/")):
                    continue            # the index page linking to itself
                window = page.text[max(0, match.start() - 300): match.end() + 300]
                dm = DATE_RE.search(normalise_text(window))
                published = _parse_date(dm.group(0)) if dm else None
                if not published:
                    continue            # no publication date: not a dated post
                activity.append({
                    "title": label[:160], "url": target, "published": published,
                    "found_on": url,
                    "evidence_id": cites.cite(page, method="dated_anchor_extraction",
                                              span=f"{published} {label[:80]}"),
                })
            if activity:
                break
    activity = _dedupe_activity(activity)[:20]
    claims.append(Claim(
        field="company_published_activity", family="dated_activity",
        value=activity or None,
        availability=Availability.AVAILABLE if activity else Availability.NOT_AVAILABLE,
        evidence_ids=sorted({a["evidence_id"] for a in activity}) or [ev_home],
        as_of=home.retrieved_at,
        note=None if activity else (shared_note if not is_own_site
                                    else "no dated post found on the company's own news pages")))

    # 7. careers surface on the company's own site, matched on whole path
    # segments so 'ejobb.no/om-oss' is not read as a careers page.
    careers: list[dict[str, Any]] = []
    if is_own_site:
        for page in own_pages:
            url = page.final_url or page.url
            if not _path_has(url, CAREER_SEGMENTS):
                continue
            careers.append({
                "url": url,
                "evidence_id": cites.cite(page, method="career_path_match",
                                          span=_title_of(page.text)),
            })
    claims.append(Claim(
        field="careers_page", family="hiring", value=careers[:3] or None,
        availability=Availability.AVAILABLE if careers else Availability.NOT_AVAILABLE,
        evidence_ids=[c["evidence_id"] for c in careers[:3]] or [ev_home],
        as_of=home.retrieved_at,
        note=None if careers else (shared_note if not is_own_site
                                   else "no careers page on the company's own site")))
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
