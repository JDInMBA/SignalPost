"""Provenance of external items.

Every published website, news, job or social item must link the exact page it
came from, and must belong to this company rather than to a parent group, a
chain, a web agency or the site theme. These are the failure modes that turn a
plausible-looking profile into a wrong-company publication.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost.envelope import Availability  # noqa: E402
from signalpost.http import Response  # noqa: E402
from signalpost.sources.website import (  # noqa: E402
    _decode, _handle_belongs_to, _names_the_company, _path_has,
    CAREER_SEGMENTS, NEWS_SEGMENTS, extract_claims,
)


def page(url: str, html: str) -> Response:
    return Response(url=url, status=200, body=html.encode(), text=html, final_url=url)


def run(org, name, pages, domain=None):
    claims, evidence = extract_claims(
        org, domain or "example.no", pages,
        {"tier": "A", "confidence": 0.99, "publish_domain": domain or "example.no"},
        "ev-web", legal_name=name)
    return {c.field: c for c in claims}, {e.id: e for e in evidence}


# -- 1. the exact page, not the homepage ---------------------------------
def test_every_item_links_the_page_it_came_from():
    pages = [
        page("https://sovikskaravik.no/", "<title>Søvik &amp; Skåravik AS</title>"
             "<a href='https://www.facebook.com/sovikskaravik'>Facebook</a>"),
        page("https://sovikskaravik.no/karriere/", "<title>Karriere - Søvik &amp; Skåravik</title>"),
    ]
    claims, ev = run("966981911", "SØVIK & SKÅRAVIK AS", pages, "sovikskaravik.no")
    careers = claims["careers_page"]
    assert careers.availability is Availability.AVAILABLE
    item = careers.value[0]
    # The citation is the careers page itself, not the homepage.
    assert ev[item["evidence_id"]].source_url == "https://sovikskaravik.no/karriere/"
    assert item["url"] == "https://sovikskaravik.no/karriere/"


# -- 2. a parent group's site is not this company's ----------------------
def test_group_site_does_not_lend_its_profiles_news_or_jobs():
    """THON SENTER BERGEN AS resolved to thon.no, the group's site.

    Publishing 'Thon gruppens halvårsrapport' as this subsidiary's news, and the
    group's careers page as its jobs, attributes the parent's activity to the
    child.
    """
    pages = [
        page("https://thon.no/", "<title>Thon Eiendom</title>"
             "<a href='https://www.facebook.com/thoneiendom'>Facebook</a>"),
        page("https://thon.no/karriere/", "<title>Karriere i Thon</title>"),
        page("https://thon.no/om/presse/",
             "<a href='https://thon.no/om/presse/halvaar'>Thon gruppens halvårsrapport for 2026</a> 12.08.2026"),
    ]
    claims, _ = run("919220252", "THON SENTER BERGEN AS", pages, "thon.no")
    for f in ("company_owned_profiles", "company_published_activity", "careers_page"):
        assert claims[f].availability is Availability.NOT_AVAILABLE, f
        assert "group or chain site" in claims[f].note


# -- 3. a chain's social accounts are not the member's -------------------
def test_chain_social_accounts_are_refused():
    assert not _handle_belongs_to("https://www.facebook.com/bdekk", "AKERSHUS DEKK AS")
    assert not _handle_belongs_to(
        "https://www.linkedin.com/company/dekk-partner-as", "AKERSHUS DEKK AS")
    assert _handle_belongs_to("https://www.facebook.com/sovikskaravik", "SØVIK & SKÅRAVIK AS")
    assert _handle_belongs_to("https://www.instagram.com/faerbergen", "FÆR AS")


# -- 4. no substring false positives -------------------------------------
def test_careers_match_whole_path_segments():
    """'ejobb.no/om-oss' contains the letters 'jobb' but is an About page."""
    assert not _path_has("http://ejobb.no/om-oss", CAREER_SEGMENTS)
    assert not _path_has("http://ejobb.no/om", CAREER_SEGMENTS)
    assert _path_has("https://x.no/ledige-stillinger", CAREER_SEGMENTS)
    assert _path_has("https://x.no/karriere/", CAREER_SEGMENTS)
    assert _path_has("https://x.no/aktuelt/", NEWS_SEGMENTS)


# -- 5. news must be a dated post, not a menu label ----------------------
def test_menu_labels_and_undated_links_are_not_news():
    pages = [
        page("https://firma.no/", "<title>Firma AS</title>"),
        page("https://firma.no/nyheter/",
             "<a href='/nyheter/'>Les mer</a>"
             "<a href='/produkter/stol'>Vår nye kontorstol i eik</a>"
             "<a href='/nyheter/ny-avtale'>Firma AS signerer ny rammeavtale</a> 03.11.2026"),
    ]
    claims, ev = run("810034882", "FIRMA AS", pages, "firma.no")
    act = claims["company_published_activity"]
    assert act.availability is Availability.AVAILABLE
    titles = [a["title"] for a in act.value]
    assert any("rammeavtale" in t for t in titles)
    assert not any("les mer" in t for t in titles)
    for a in act.value:
        assert a["published"] and a["evidence_id"] in ev


def test_html_entities_are_decoded():
    assert _decode("halv&#xe5;rsrapport for 2026") == "halvårsrapport for 2026"
    assert _decode("S&oslash;vik &amp; Sk&aring;ravik") == "Søvik & Skåravik"


def test_name_gate_separates_the_company_from_its_group():
    assert _names_the_company("<title>Thon Eiendom</title>",
                              "THON SENTER BERGEN AS", "919220252", "thon.no") is False
    assert _names_the_company("<title>eJobb</title>",
                              "AQUACULTURETALENT AS", "916725981", "ejobb.no") is False
    assert _names_the_company("<title>Søvik &amp; Skåravik AS</title>",
                              "SØVIK & SKÅRAVIK AS", "966981911", "sovikskaravik.no") is True
    # The organisation number on the page settles it regardless of naming.
    assert _names_the_company("Org.nr 919 220 252", "THON SENTER BERGEN AS",
                              "919220252", "thon.no") is True
