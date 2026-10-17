"""Norwegian normalisation rules that identity decisions depend on."""
from __future__ import annotations

import pytest

from signalpost.normalize import (
    domain_candidates_from_name, email_domain, find_org_numbers, is_platform_host,
    looks_like_address, mod11_valid, normalise_name, normalise_phone, registered_domain,
)


@pytest.mark.parametrize("org,expected", [
    ("810034882", True), ("923609016", True),
    ("12345678", False), ("1234567890", False), ("81003488X", False),
    ("810034883", False),           # correct digits, wrong check digit
])
def test_mod11(org, expected):
    assert mod11_valid(org) is expected


def test_find_org_numbers_handles_norwegian_formatting():
    # 111111112 carries a wrong check digit, so it must be discarded even though
    # it looks like an organisation number.
    text = "org.nr: 810 034 882, mva 923.609.016, noise: 111111112"
    assert find_org_numbers(text) == {"810034882", "923609016"}


def test_find_org_numbers_rejects_longer_digit_runs():
    assert find_org_numbers("account 12345678901234") == set()


def test_legal_form_is_stripped_from_names():
    assert normalise_name("SANDNES ELEKTRISKE AS") == "sandnes elektriske"
    assert normalise_name("NORDIC WILDFISH SALES ASA") == "nordic wildfish sales"
    assert normalise_name("JOBZONE NORGE AS AVD BODØ") == "jobzone norge"


def test_address_shaped_names_are_flagged():
    # These are real property companies named after their own address; a match on
    # someone else's contact page is a quotation, not an identification.
    assert looks_like_address("SKJEBERGVEIEN 25 AS")
    assert looks_like_address("GAMLE STANGNESVEI 3 AS")
    assert looks_like_address("STORGATA 12 AS")
    assert not looks_like_address("SKILTHUSET AS")
    assert not looks_like_address("NORDIC WILDFISH SALES AS")
    # A real trading name that merely contains a digit must stay matchable.
    assert not looks_like_address("SYSTEM 3 CONSULTING AS")


def test_email_domain_rejects_freemail():
    assert email_domain("post@firma.no") == "firma.no"
    assert email_domain("daglig.leder@gmail.com") == ""
    assert email_domain("a@online.no") == ""
    assert email_domain("not-an-email") == ""


def test_registered_domain_and_platform_detection():
    assert registered_domain("https://www.shop.firma.no/x?y=1") == "firma.no"
    assert registered_domain("post@firma.no") == "firma.no"
    assert is_platform_host("https://www.linkedin.com/company/x")
    assert not is_platform_host("https://firma.no")


def test_phone_normalisation_is_country_code_agnostic():
    assert normalise_phone("+47 51 68 57 00") == "51685700"
    assert normalise_phone("51685700") == "51685700"
    assert normalise_phone("123") == ""


def test_domain_candidates_fold_norwegian_letters():
    assert "hogri.no" in domain_candidates_from_name("HØGRI AS")
    assert "sandneselektriske.no" in domain_candidates_from_name("SANDNES ELEKTRISKE AS")


def test_org_number_not_matched_inside_a_longer_grouped_number():
    """A share capital of 5,923,609,016 must not yield organisation number 923609016.

    The nine-digit window is mod-11 valid, so before the lookarounds were widened
    it registered as a conflicting organisation number and vetoed correct website
    candidates.
    """
    assert find_org_numbers("aksjekapital kr 5 923 609 016") == set()
    assert find_org_numbers("omsetning 12 345 678 901 kroner") == set()
    # The same number on its own still resolves.
    assert find_org_numbers("Org.nr: 923 609 016") == {"923609016"}
    assert find_org_numbers("orgnr 923609016.") == {"923609016"}
