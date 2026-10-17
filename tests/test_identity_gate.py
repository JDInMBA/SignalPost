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
