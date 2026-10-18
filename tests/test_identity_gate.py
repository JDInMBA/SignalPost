"""The gate that protects qualification."""
from __future__ import annotations

from signalpost.identity import Candidate, IdentityGate, Tier

ENTITY = {
    "organisation_number": "810034882",
    "legal_name": "SANDNES ELEKTRISKE AS",
    "email": "post@sandneselektriske.no",
    "phone": "51 68 57 00",
    "business_address": {"adresse": ["Industriveien 1"], "postnummer": "4313",
                         "poststed": "SANDNES"},
}


def verdict(html: str, domain: str = "sandneselektriske.no", entity=None):
    return IdentityGate().evaluate(entity or ENTITY, Candidate(
        domain=domain, channel="test", pages=[(f"https://{domain}/", html)]))


def test_org_number_on_page_is_decisive():
    v = verdict("<title>Sandnes Elektriske</title><footer>Org.nr 810 034 882</footer>")
    assert v.accepted and v.tier is Tier.ORG_NUMBER and v.confidence >= 0.99
    assert v.proof_url and v.proof_span


def test_conflicting_org_number_vetoes_publication():
    v = verdict("<title>Et annet firma</title><footer>Orgnr: 923 609 016</footer>")
    assert not v.accepted
    assert "923609016" in v.conflicts


def test_own_org_number_overrides_a_conflict():
    # A page may legitimately list several entities, e.g. a group contact page.
    v = verdict("<title>Konsern</title><p>810 034 882 og 923 609 016</p>")
    assert v.accepted and v.tier is Tier.ORG_NUMBER


def test_registry_email_domain_needs_corroboration():
    bare = verdict("<title>Noe helt annet</title><p>ingen spor</p>")
    assert not bare.accepted
    corroborated = verdict("<title>Sandnes Elektriske AS</title><p>Tlf: 51 68 57 00</p>")
    assert corroborated.accepted and corroborated.tier is Tier.REGISTRY_EMAIL


def test_name_only_in_body_text_is_not_enough():
    # The portfolio-page trap: another company listing this one as a client.
    v = verdict("<title>Vare kunder</title><p>sandnes elektriske er en av vare kunder</p>",
                domain="annetfirma.no")
    assert not v.accepted


def test_address_shaped_name_cannot_carry_a_match():
    entity = {**ENTITY, "organisation_number": "991313494",
              "legal_name": "SKJEBERGVEIEN 25 AS", "email": "post@directa.no",
              "phone": None, "business_address": {}}
    v = verdict("<title>Directa</title><p>Vi holder til i Skjebergveien 25</p>",
                domain="directa.no", entity=entity)
    assert not v.accepted
    assert any("address-shaped" in r for r in v.reasons)


def test_parked_and_platform_candidates_are_refused():
    assert not verdict("<html><body>Dette domenet er til salgs</body></html>").accepted
    assert not verdict("<title>x</title>", domain="linkedin.com").accepted


def test_no_pages_means_no_publication():
    v = IdentityGate().evaluate(ENTITY, Candidate(domain="x.no", channel="test", pages=[]))
    assert not v.accepted and v.confidence == 0.0


def test_a_single_locator_is_not_enough_for_the_email_tier():
    # ENVION GROUP AS on the validation split: registry e-mail mail@fjordkick.no,
    # corroborated only by the founder's mobile, which is shared across their
    # other ventures. One locator must not publish.
    entity = {"organisation_number": "925903817", "legal_name": "ENVION GROUP AS",
              "email": "mail@fjordkick.no", "mobile": "979 62 230",
              "business_address": {}}
    v = verdict("<title>Fjordkick</title><p>Ring 979 62 230</p>",
                domain="fjordkick.no", entity=entity)
    assert not v.accepted

    # Two independent locators do publish.
    entity2 = {**entity, "business_address": {"adresse": ["Gjertrudvegen 11D"],
                                              "postnummer": "5353", "poststed": "STRAUME"}}
    v2 = verdict("<title>Fjordkick</title><p>Ring 979 62 230, Gjertrudvegen 11D, 5353 Straume</p>",
                 domain="fjordkick.no", entity=entity2)
    assert v2.accepted and v2.tier is Tier.REGISTRY_EMAIL


def test_a_declared_registry_website_blocks_other_domains():
    # If the company filed smartbloc.no as its website, fjordkick.no needs the
    # organisation number before it can be published.
    entity = {"organisation_number": "925903817", "legal_name": "ENVION GROUP AS",
              "website": "smartbloc.no", "email": "mail@fjordkick.no",
              "mobile": "979 62 230",
              "business_address": {"adresse": ["Gjertrudvegen 11D"],
                                   "postnummer": "5353", "poststed": "STRAUME"}}
    v = verdict("<title>Fjordkick</title><p>979 62 230, Gjertrudvegen 11D, 5353 Straume</p>",
                domain="fjordkick.no", entity=entity)
    assert not v.accepted
    assert any("declares smartbloc.no" in r for r in v.reasons)

    # The declared domain itself still publishes at tier B.
    ok = verdict("<title>Smartbloc</title>", domain="smartbloc.no", entity=entity)
    assert ok.accepted and ok.tier is Tier.REGISTRY_WEBSITE

    # And the organisation number still overrides it.
    proof = verdict("<title>Fjordkick</title><footer>Org.nr 925 903 817</footer>",
                    domain="fjordkick.no", entity=entity)
    assert proof.accepted and proof.tier is Tier.ORG_NUMBER


def test_redirect_to_the_registry_declared_website_is_tier_b():
    """A candidate that redirects to the declared website is the same site.

    fusetools.com 301s to https://fuse.no/, which is the website FUSETOOLS AS
    filed with the registry. Matching only the requested domain missed that and
    fell back to the weaker e-mail tier.
    """
    entity = {"organisation_number": "997001877", "legal_name": "FUSETOOLS AS",
              "website": "www.fuse.no", "email": "mariia@fusetools.com",
              "business_address": {}}
    v = IdentityGate().evaluate(entity, Candidate(
        domain="fusetools.com", channel="registry_email_domain",
        pages=[("https://fuse.no/", "<title>Fuse</title>")]))
    assert v.accepted and v.tier is Tier.REGISTRY_WEBSITE
    assert v.signals.get("redirected_to") == "fuse.no"


def test_declared_website_rule_uses_the_landed_domain():
    """The declared-website veto must judge where the crawl ended up."""
    entity = {"organisation_number": "925903817", "legal_name": "ENVION GROUP AS",
              "website": "smartbloc.no", "email": "mail@fjordkick.no",
              "mobile": "979 62 230", "business_address": {}}
    # Lands somewhere that is not the declared site: still refused.
    v = IdentityGate().evaluate(entity, Candidate(
        domain="fjordkick.no", channel="registry_email_domain",
        pages=[("https://fjordkick.no/", "<title>Fjordkick</title><p>979 62 230</p>")]))
    assert not v.accepted
    assert any("declares smartbloc.no" in r for r in v.reasons)


def test_redirect_to_a_franchise_portal_is_refused():
    """STILBYGG KONGSBERG AS redirects to norgeshus.no, its building chain.

    The registry e-mail domain attests stilbygg.no and says nothing about the
    chain portal, so the destination must not be published as this company's own
    site. Matching the e-mail signal against either domain published it.
    """
    entity = {"organisation_number": "981634632", "legal_name": "STILBYGG KONGSBERG AS",
              "email": "post@stilbygg.no", "business_address": {}}
    v = IdentityGate().evaluate(entity, Candidate(
        domain="stilbygg.no", channel="registry_email_domain",
        pages=[("https://norgeshus.no/", "<title>Norgeshus</title><p>Stilbygg Kongsberg</p>")]))
    assert not v.accepted
    assert v.signals.get("redirected_to") == "norgeshus.no"
    assert any("does not attest" in r for r in v.reasons)


def test_redirect_is_allowed_when_the_destination_is_proven():
    """The organisation number on the destination still publishes it."""
    entity = {"organisation_number": "981634632", "legal_name": "STILBYGG KONGSBERG AS",
              "email": "post@stilbygg.no", "business_address": {}}
    v = IdentityGate().evaluate(entity, Candidate(
        domain="stilbygg.no", channel="registry_email_domain",
        pages=[("https://byggmester.no/", "<title>X</title><footer>Org.nr 981 634 632</footer>")]))
    assert v.accepted and v.tier is Tier.ORG_NUMBER


def test_declared_website_stands_even_when_it_redirects():
    """A declared domain that forwards to a chain portal is still the company's.

    STILBYGG KONGSBERG AS filed stilbygg.no; it forwards to its building chain.
    The registry attests stilbygg.no, so that is what is published - never the
    destination.
    """
    entity = {"organisation_number": "981634632", "legal_name": "STILBYGG KONGSBERG AS",
              "website": "stilbygg.no", "email": "post@stilbygg.no",
              "business_address": {}}
    v = IdentityGate().evaluate(entity, Candidate(
        domain="stilbygg.no", channel="registry_website",
        pages=[("https://norgeshus.no/", "<title>Norgeshus</title>")]))
    assert v.accepted and v.tier is Tier.REGISTRY_WEBSITE
    assert v.publish_domain == "stilbygg.no"

    # Without the registry declaring it, the destination is still refused.
    bare = {k: val for k, val in entity.items() if k != "website"}
    v2 = IdentityGate().evaluate(bare, Candidate(
        domain="stilbygg.no", channel="registry_email_domain",
        pages=[("https://norgeshus.no/", "<title>Norgeshus</title>")]))
    assert not v2.accepted
