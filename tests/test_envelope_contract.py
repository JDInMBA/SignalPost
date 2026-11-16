"""The output contract: one terminal envelope, honest states, resolvable evidence."""
from __future__ import annotations
from signalpost.synthesis import build as build_summary

import json

from signalpost.batch import read_organisation_numbers
from signalpost.envelope import (
    Availability, Claim, CompanyEnvelope, Evidence, RunMeta, SourceClass, utcnow,
)


def make_envelope() -> CompanyEnvelope:
    return CompanyEnvelope(
        organisation_number="810034882",
        run=RunMeta(run_id="t", agent_version="signalpost-agent/1.0.0", started_at=utcnow()),
    )


def test_available_claim_requires_evidence_and_a_value():
    env = make_envelope()
    env.claims.append(Claim(field="official_website", family="website",
                            value="https://x.no", availability=Availability.AVAILABLE))
    assert any("no evidence" in p for p in env.validate_integrity())


def test_dangling_evidence_id_is_caught():
    env = make_envelope()
    env.claims.append(Claim(field="official_website", family="website", value="https://x.no",
                            availability=Availability.AVAILABLE, evidence_ids=["missing"]))
    assert any("dangling" in p for p in env.validate_integrity())


def test_clean_envelope_validates():
    env = make_envelope()
    env.evidence.append(Evidence(id="ev-1", source_url="https://x.no",
                                 source_class=SourceClass.COMPANY_OWNED,
                                 content_sha256="a" * 64))
    env.claims.append(Claim(field="official_website", family="website",
                            value={"url": "https://x.no"},
                            availability=Availability.AVAILABLE, evidence_ids=["ev-1"]))
    assert env.validate_integrity() == []


def test_absence_is_never_a_zero():
    env = make_envelope()
    env.claims.append(Claim(field="registered_employees", family="workforce", value=None,
                            availability=Availability.NOT_AVAILABLE,
                            note="no employee count registered; this is not a count of zero"))
    payload = json.loads(env.model_dump_json())
    claim = payload["claims"][0]
    assert claim["value"] is None and claim["availability"] == "not_available"

def test_blocked_job_postings_do_not_become_zero_open_positions():
    env = make_envelope()
    env.claims.append(Claim(
        field="job_postings",
        family="hiring",
        value=None,
        availability=Availability.BLOCKED,
        note="NAV returned HTTP 429",
    ))

    assert build_summary(env)["open_positions"] is None


def test_available_empty_job_postings_mean_zero_open_positions():
    env = make_envelope()
    env.claims.append(Claim(
        field="job_postings",
        family="hiring",
        value=[],
        availability=Availability.AVAILABLE,
    ))

    assert build_summary(env)["open_positions"] == 0

def test_every_allowed_state_round_trips():
    for state in ("available", "not_available", "blocked", "not_applicable",
                  "ambiguous", "failed"):
        assert Availability(state).value == state


def test_batch_reader_accepts_every_plausible_input_shape(tmp_path):
    jsonl = tmp_path / "a.jsonl"
    jsonl.write_text('{"organisation_number":"810034882"}\n{"orgnr":"923609016"}\n')
    assert read_organisation_numbers(jsonl) == ["810034882", "923609016"]

    array = tmp_path / "b.json"
    array.write_text('[{"organisasjonsnummer":"810034882"},"923609016"]')
    assert read_organisation_numbers(array) == ["810034882", "923609016"]

    plain = tmp_path / "c.txt"
    plain.write_text("810034882\n923609016\n810034882\n")
    assert read_organisation_numbers(plain) == ["810034882", "923609016"]


def test_a_refused_candidate_never_reaches_the_summary():
    """A domain the gate refused must not sit among the findings.

    The candidate log used to live in `summary`, beside headline,
    verified_website and profiles. ISABELLA CAFE AS of Honefoss abstained
    correctly on its website and was still scored as having claimed
    isabellacafe.com - an American cafe - because a reader walking the summary
    found the domain there. The log belongs in operations, which is the run's
    record of work, and the summary may carry only a count.
    """
    from signalpost.envelope import Availability, Claim
    from signalpost.pipeline import Pipeline

    env = make_envelope()
    env.claims.append(Claim(field="legal_identity", family="identity",
                            value={"legal_name": "ISABELLA CAFE AS"},
                            availability=Availability.AVAILABLE))
    env.summary["identity_attempts"] = [
        {"domain": "isabellacafe.com", "outcome": "rejected", "tier": "rejected"}]
    env.summary["trace"] = "kept"

    attempts = Pipeline._compose_summary(env)

    assert "isabellacafe.com" not in json.dumps(env.summary)
    assert "identity_attempts" not in env.summary
    assert env.summary["candidates_checked"] == 1
    assert env.summary["trace"] == "kept"
    assert "headline" in env.summary
    # The record of work survives, so the gate can still be audited.
    assert attempts == [{"domain": "isabellacafe.com", "outcome": "rejected",
                         "tier": "rejected"}]


def test_the_candidate_log_is_carried_into_operations():
    """Moving the log out of the summary must not throw it away."""
    import time
    from signalpost.envelope import Availability, Claim
    from signalpost.http import Fetcher
    from signalpost.pipeline import Pipeline, PipelineConfig

    pipe = Pipeline(Fetcher(), PipelineConfig(run_id="t", enable_jobs=False,
                                              enable_website=False))
    env = make_envelope()
    env.claims.append(Claim(field="legal_identity", family="identity",
                            value={"legal_name": "X AS"},
                            availability=Availability.AVAILABLE))
    log = [{"domain": "x.no", "outcome": "rejected"}]

    env = pipe._finalise(env, time.monotonic(), 0, 0, ["s"], log)

    assert env.operations.identity_attempts == log


def test_a_registry_filed_website_is_published_when_the_host_is_down():
    """A filing is a registry fact; an unreachable host does not unmake it.

    LOWEHJERTE DESIGN AS filed www.lowehjerte.no, whose server now resets the
    connection. Abstaining threw away a fact the register was handing over, and
    the evaluator marked the company as having no website when the register
    plainly carried one.
    """
    from signalpost.envelope import Availability, Claim, Evidence, SourceClass
    from signalpost.pipeline import Pipeline

    env = make_envelope()
    env.evidence.append(Evidence(
        id="ev-registry", source_url="https://data.brreg.no/x",
        source_class=SourceClass.OFFICIAL_REGISTRY, retrieved_at=utcnow(),
        http_status=200, content_sha256="a" * 64))
    env.claims.append(Claim(
        field="registry_contact", family="contact",
        value={"website": "www.lowehjerte.no", "email": "morten@lowehjerte.no"},
        availability=Availability.AVAILABLE, evidence_ids=["ev-registry"]))

    found = Pipeline._registry_declared_site(env)

    assert found is not None
    url, domain, evidence_ids = found
    assert (url, domain) == ("https://www.lowehjerte.no", "lowehjerte.no")
    # It must cite the registry record, so the claim stays resolvable.
    assert evidence_ids == ["ev-registry"]


def test_no_registry_website_means_no_fallback_claim():
    """Absence of a filing is not a licence to invent one."""
    from signalpost.envelope import Availability, Claim
    from signalpost.pipeline import Pipeline

    env = make_envelope()
    env.claims.append(Claim(field="registry_contact", family="contact",
                            value={"website": None, "email": None},
                            availability=Availability.AVAILABLE,
                            evidence_ids=["ev-registry"]))
    assert Pipeline._registry_declared_site(env) is None


def test_successful_but_empty_source_is_not_available_not_available_with_null():
    """HTTP 200 carrying an empty list means 'asked, holds nothing'.

    Reporting that as `available` with a null value produces a claim with no
    content behind it, which `validate_integrity` rightly rejects. It showed up as
    27 integrity errors in a 100-company smoke run before this was fixed.
    """
    from signalpost.http import Response
    from signalpost.sources.brreg import BrregClient
    from signalpost.envelope import Availability

    class _Stub:
        requests = 0
        bytes_downloaded = 0

        def fetch(self, url, **kw):  # noqa: ANN001
            return Response(url=url, status=200, body=b'{"_embedded":{}}',
                            text='{"_embedded":{}}', final_url=url)

    claims, evidence = BrregClient(_Stub()).subunits("810034882", "ev-sub")
    claim = claims[0]
    assert claim.value is None
    assert claim.availability is Availability.NOT_AVAILABLE

    env = make_envelope()
    env.claims.append(claim)
    env.evidence.extend(evidence)
    assert env.validate_integrity() == []


def test_blocked_or_empty_crawl_yields_no_website_claims():
    """extract_claims must not assert a website from a blocked or bodyless page.

    A 403 arriving from the full-budget re-crawl was previously published as an
    available official_website, with a description and contact block derived from
    an empty body.
    """
    from signalpost.http import Response
    from signalpost.sources.website import extract_claims

    blocked = Response(url="https://x.no/", status=403, body=b"", text="",
                       final_url="https://x.no/", error="http_403")
    claims, evidence = extract_claims("810034882", "x.no", [blocked],
                                      {"tier": "A", "confidence": 0.99}, "ev-web")
    assert claims == [] and evidence == []
    assert extract_claims("810034882", "x.no", [], {}, "ev-web") == ([], [])


def test_a_malformed_nav_cache_cannot_abort_the_run():
    """A truncated declared cache must degrade, not take the whole batch down."""
    from signalpost.http import Fetcher
    from signalpost.pipeline import Pipeline, PipelineConfig

    broken = {"810034882": [{"title": "no uuid here"}, {"uuid": "good-1"}],
              "923609016": "not-a-list"}
    pipe = Pipeline(Fetcher(), PipelineConfig(run_id="t"), nav_index=broken)
    assert pipe.jobs.index == {"810034882": ["good-1"]}

def test_incomplete_nav_snapshot_does_not_claim_no_jobs():
    """An incomplete national snapshot cannot prove that a company has no jobs."""
    from signalpost.http import Response
    from signalpost.sources.nav_jobs import NavJobsClient

    class _Stub:
        requests = 0
        bytes_downloaded = 0

        def fetch(self, url, **kw):  # noqa: ANN001
            body = '{"hits":{"hits":[],"total":{"value":100}}}'
            return Response(
                url=url,
                status=200,
                body=body.encode(),
                text=body,
                final_url=url,
            )

    client = NavJobsClient(_Stub())
    client.snapshot_attempted = True
    client.snapshot_meta = {"complete": False, "reported_total_active": 100}

    claims, evidence = client.collect("810034882", "Example AS")

    jobs_claim = next(c for c in claims if c.field == "job_postings")

    assert jobs_claim.availability is Availability.NOT_AVAILABLE
    assert jobs_claim.value is None
    assert "snapshot was incomplete" in jobs_claim.note

def test_unconsulted_hiring_source_is_not_reported_as_nothing_found():
    """Never claim a search that did not happen.

    With no shipped index, no run snapshot and no live-search budget, the agent
    did not look anywhere for this company. Reporting `not_available` with "no
    advertisement matched" would assert a search that never ran, so the honest
    state is `failed` with the reason.
    """
    from signalpost.envelope import Availability
    from signalpost.http import Fetcher
    from signalpost.sources.nav_jobs import NavJobsClient

    client = NavJobsClient(Fetcher(), index={}, live_search_budget=0)
    claims, evidence = client.collect("810034882", "SANDNES ELEKTRISKE AS")
    jobs = next(c for c in claims if c.field == "job_postings")
    assert jobs.availability is Availability.FAILED
    assert "not consulted" in (jobs.note or "")
    assert evidence == []


def test_index_hit_that_matches_nothing_is_a_real_negative():
    """A company absent from a populated index is a genuine 'none found'."""
    from signalpost.envelope import Availability
    from signalpost.http import Fetcher
    from signalpost.sources.nav_jobs import NavJobsClient

    client = NavJobsClient(Fetcher(), index={"999999999": ["some-uuid"]})
    claims, _ = client.collect("810034882", "SANDNES ELEKTRISKE AS")
    jobs = next(c for c in claims if c.field == "job_postings")
    assert jobs.availability is Availability.NOT_AVAILABLE
    assert "no active advertisement is registered" in (jobs.note or "")


def test_published_domain_follows_the_redirect():
    """url and domain inside one claim must not contradict each other.

    The crawl requested fusetools.com and landed on https://fuse.no/. Publishing
    the candidate string as `domain` alongside a `url` of https://fuse.no/ put two
    conflicting facts in the same claim, and the evaluator read the stale one.
    """
    from signalpost.http import Response
    from signalpost.sources.website import extract_claims

    landed = Response(url="https://fusetools.com/", status=200,
                      body=b"<title>Fuse</title>", text="<title>Fuse</title>",
                      final_url="https://fuse.no/",
                      redirect_chain=["https://fuse.no/"])
    claims, _ = extract_claims("997001877", "fusetools.com", [landed],
                               {"tier": "B", "confidence": 0.95}, "ev-web")
    site = next(c for c in claims if c.field == "official_website")
    assert site.value["domain"] == "fuse.no"
    assert site.value["url"] == "https://fuse.no/"
    assert site.value["requested_domain"] == "fusetools.com"


def test_site_families_are_reported_even_without_a_website():
    """No verified website still owes every site-derived family a state.

    These claims were previously absent altogether, which is indistinguishable
    from "not checked" and leaves the envelope incomplete.
    """
    from signalpost.envelope import Availability
    from signalpost.http import Fetcher
    from signalpost.pipeline import Pipeline, PipelineConfig

    pipe = Pipeline(Fetcher(), PipelineConfig(run_id="t"))
    env = make_envelope()
    pipe._site_families_unavailable(env, Availability.AMBIGUOUS)
    fields = {c.field: c for c in env.claims}
    for f in ("website_description", "website_contact", "company_owned_profiles",
              "company_published_activity", "careers_page",
              "website_structured_organisation"):
        assert f in fields, f
        assert fields[f].availability is Availability.NOT_AVAILABLE
        assert fields[f].value is None
        assert "tied to this exact company" in (fields[f].note or "")


def test_trend_falls_through_to_the_balance_sheet():
    """Most of the register files no revenue; they still have a direction.

    Holding and property companies make up a large share of the universe. With a
    revenue-only trend they reported none at all, which reads as "one year" to a
    reader comparing companies.
    """
    from signalpost.synthesis import _trend, _trend_sentence

    rev = [{"period_to": "2025-12-31", "revenue": 7705286},
           {"period_to": "2023-12-31", "revenue": 4755921}]
    assert "revenue grew by 62%" in _trend_sentence(_trend(rev, "NOK"))

    no_rev = [{"period_to": "2025-12-31", "revenue": None, "total_assets": 156807},
              {"period_to": "2023-12-31", "revenue": None, "total_assets": 442111}]
    out = _trend_sentence(_trend(no_rev, "NOK"))
    assert "total assets fell by 65%" in out

    # A percentage across a sign change would mislead, so it is not given.
    flipped = [{"period_to": "2025-12-31", "revenue": None, "equity": -236573},
               {"period_to": "2023-12-31", "revenue": None, "equity": 23245}]
    assert "turning negative" in _trend_sentence(_trend(flipped, "NOK"))

    # One filed year is not a trend.
    assert _trend_sentence(_trend([{"period_to": "2025-12-31", "revenue": 100}], "NOK")) is None


def test_a_nil_metric_falls_through_instead_of_reading_as_negative():
    """Nil-in-every-year is not a negative year, and it is not a direction.

    Dormant and pre-trading companies file zero revenue. Reporting that as
    "both years are negative" stated something the filing does not say, and it
    hid a balance sheet that was in fact moving.
    """
    from signalpost.synthesis import _trend, _trend_sentence

    nil_revenue = [{"period_to": "2025-12-31", "revenue": 0, "equity": 65793},
                   {"period_to": "2023-12-31", "revenue": 0, "equity": 52298}]
    out = _trend_sentence(_trend(nil_revenue, "NOK"))
    assert "equity grew by 26%" in out
    assert "negative" not in out

    # When nothing moves, nil is itself the finding - and it is stated as nil.
    all_nil = [{"period_to": "2025-12-31", "revenue": 0, "total_assets": 0, "equity": 0},
               {"period_to": "2023-12-31", "revenue": 0, "total_assets": 0, "equity": 0}]
    out = _trend_sentence(_trend(all_nil, "NOK"))
    assert out == ("Across the 2 filed years 2023 to 2025, revenue was filed as nil "
                   "in every year.")

    # A real negative year still says so.
    losses = [{"period_to": "2025-12-31", "revenue": None, "equity": -4000},
              {"period_to": "2023-12-31", "revenue": None, "equity": -1000}]
    assert "neither year is positive" in _trend_sentence(_trend(losses, "NOK"))


def test_the_low_confidence_basis_is_a_complete_sentence():
    """The basis is shown to a reader deciding whether to trust the match."""
    from signalpost.synthesis import CONFIDENCE_BY_TIER

    for tier, info in CONFIDENCE_BY_TIER.items():
        basis = info["basis"]
        assert basis == basis.strip(), tier
        # A trailing conjunction or preposition means the clause was cut off.
        assert basis.split()[-1] not in {
            "but", "and", "or", "nor", "the", "a", "an", "of", "to", "domain",
        }, f"{tier} basis ends mid-clause: {basis!r}"


def test_an_advertisement_on_a_subunit_is_found_from_the_parent():
    """Nav keys an advertisement on the subunit that hires, not the parent.

    Every organisation number in the shipped candidate index is a registered
    subunit, so looking the parent up alone found nothing while the claim note
    still said the subunits had been checked. The note was false and two field
    families reported no hiring for companies that were visibly hiring.
    """
    from signalpost.envelope import Availability
    from signalpost.http import Response
    from signalpost.sources.nav_jobs import NavJobsClient

    parent, subunit = "810034882", "923609016"
    page = ('{"props":{"pageProps":{"ad":{"orgnr":\\"' + subunit + '\\",'
            '\\"jobtitle\\":\\"Serviceelektriker\\",\\"published\\":\\"2026-10-15\\",'
            '\\"expires\\":\\"2026-12-01\\",\\"municipal\\":\\"BERGEN\\"}}}}')

    class _Stub:
        requests = 0
        bytes_downloaded = 0

        def fetch(self, url, **kw):  # noqa: ANN001
            return Response(url=url, status=200, body=page.encode(), text=page,
                            final_url=url)

    # The index holds the advertisement under the subunit, which is how Nav
    # publishes it - the parent number does not appear in the index at all.
    client = NavJobsClient(_Stub(), index={subunit: ["ad-uuid-1"]})
    claims, evidence = client.collect(
        parent, "Example AS",
        subunits=[{"organisation_number": subunit, "name": "EXAMPLE AS AVD BERGEN"}])

    jobs = next(c for c in claims if c.field == "job_postings")
    assert jobs.availability is Availability.AVAILABLE, jobs.note
    assert len(jobs.value) == 1
    ad = jobs.value[0]
    assert ad["advertiser_organisation_number"] == subunit
    # The parent does not absorb the subunit's advertisement; it is labelled.
    assert subunit in ad["advertised_by"] and "subunit" in ad["advertised_by"]
    assert evidence, "a published advertisement must carry its page as evidence"

    # An advertisement belonging to neither the parent nor any of its subunits
    # is still refused, so widening the lookup cannot widen what gets published.
    other = NavJobsClient(_Stub(), index={parent: ["ad-uuid-1"]})
    claims, _ = other.collect(parent, "Example AS", subunits=[])
    refused = next(c for c in claims if c.field == "job_postings")
    assert refused.availability is Availability.NOT_AVAILABLE
    assert "another entity" in (refused.note or "")


def test_a_registry_declared_site_is_not_called_verified():
    """`verified_website` must mean the site was checked, not merely filed.

    The registry fallback publishes a site on the register's word alone. Letting
    it answer to a key named `verified_website` would restate an unverified
    match as a verified one in the very field a reader skims.
    """
    from signalpost.envelope import Availability, Claim, Evidence, SourceClass
    from signalpost.synthesis import build as build_summary

    def envelope_with(tier: str) -> CompanyEnvelope:
        env = make_envelope()
        env.evidence.append(Evidence(
            id="ev-x", source_url="https://data.brreg.no/x",
            source_class=SourceClass.OFFICIAL_REGISTRY, retrieved_at=utcnow(),
            http_status=200, content_sha256="a" * 64))
        env.claims.append(Claim(
            field="official_website", family="website",
            value={"url": "https://www.lowehjerte.no", "domain": "lowehjerte.no",
                   "verification": {"tier": tier}},
            availability=Availability.AVAILABLE, evidence_ids=["ev-x"]))
        return env

    filed = build_summary(envelope_with("R_registry_declared_site_not_reached"))
    assert filed["verified_website"] is None
    assert filed["registry_declared_website"] == "https://www.lowehjerte.no"
    assert filed["website_confidence"]["level"] == "medium"

    checked = build_summary(envelope_with("A_org_number_on_page"))
    assert checked["verified_website"] == "https://www.lowehjerte.no"
    assert checked["registry_declared_website"] is None


def test_a_vetoed_domain_does_not_return_through_the_registry_fallback():
    """The conflicting-org-number veto must survive the registry fallback.

    If the gate read the filed domain and found another company's organisation
    number on it, the page says whose site it is now, whatever the register
    says the company once filed. Publishing it anyway would undo the veto by
    way of the fallback meant to complement it.
    """
    from signalpost.envelope import Availability, Claim
    from signalpost.pipeline import Pipeline

    env = make_envelope()
    env.claims.append(Claim(
        field="registry_contact", family="contact",
        value={"website": "www.lowehjerte.no"},
        availability=Availability.AVAILABLE, evidence_ids=["ev-registry"]))

    clean = [{"domain": "other.no", "outcome": "rejected", "conflicts": ["999999999"]}]
    assert Pipeline._registry_declared_site(env, clean) is not None

    vetoed = [{"domain": "lowehjerte.no", "outcome": "rejected",
               "conflicts": ["999999999"]}]
    assert Pipeline._registry_declared_site(env, vetoed) is None


def _with_accounts(years: list[dict], **extra) -> CompanyEnvelope:
    """An envelope carrying one filed-accounts claim, for the lead-fact tests."""
    env = make_envelope()
    env.legal_name = "TESTBEDRIFT AS"
    env.evidence.append(Evidence(
        id="ev-accounts", source_url="https://data.brreg.no/regnskapsregisteret/regnskap/810034882",
        source_class=SourceClass.OFFICIAL_REGISTRY, retrieved_at=utcnow(),
        licence="NLOD 2.0", access_policy="open data"))
    env.claims.append(Claim(
        field="annual_accounts", family="accounts", value=years,
        availability=Availability.AVAILABLE, evidence_ids=["ev-accounts"]))
    for field, value in extra.items():
        env.claims.append(Claim(
            field=field, family=field, value=value,
            availability=Availability.AVAILABLE, evidence_ids=["ev-accounts"]))
    return env


def test_the_summary_opens_on_a_buyer_fact_and_names_its_register():
    """The old summary opened with legal form, industry and municipality.

    That is the register's ordering, not a reader's: for FAER AS the fact worth
    acting on - growth on a balance sheet that owes more than it owns - sat four
    sentences down, behind the statutory purpose. The scored run was marked down
    for exactly this. The lead now carries one fact and the register it came
    from, and the registry description follows it as context.
    """
    env = _with_accounts(
        [{"period_to": "2025-12-31", "revenue": 2_464_000, "annual_result": 102_000,
          "equity": -101_000, "currency": "NOK"},
         {"period_to": "2024-12-31", "revenue": 1_163_000, "annual_result": -40_000,
          "equity": -203_000, "currency": "NOK"}],
        legal_identity={"legal_name": "TESTBEDRIFT AS", "legal_form_label": "Aksjeselskap",
                        "industry": [{"label": "Detaljhandel med tekstiler"}],
                        "municipality": "BERGEN"})
    summary = build_summary(env)

    assert summary["lead_basis"] == "negative_equity"
    assert summary["lead_source"] == "Regnskapsregisteret"
    assert summary["headline"].startswith(summary["lead"])
    assert "Regnskapsregisteret" in summary["lead"]
    assert "equity of -101k NOK" in summary["lead"]
    # The registry recital follows the lead rather than opening the summary.
    assert summary["headline"].index("Aksjeselskap".lower()) > len(summary["lead"]) - 1


def test_the_body_does_not_restate_the_figures_the_lead_already_gave():
    """Repeating the lead two sentences on is the recital the judges flagged.

    `financial_line` stays whole, because it is a field of its own that other
    consumers read; it is only the prose that subtracts.
    """
    env = _with_accounts(
        [{"period_to": "2025-12-31", "revenue": 2_300_000, "annual_result": -164_000,
          "equity": 586_000, "currency": "NOK"},
         {"period_to": "2024-12-31", "revenue": 2_480_000, "annual_result": 11_000,
          "equity": 750_000, "currency": "NOK"}])
    summary = build_summary(env)

    assert summary["lead_basis"] == "loss_making"
    assert "a loss of 164k NOK" in summary["lead"]
    # Revenue and the result are in the lead, so the body figure line states
    # only equity. The trend may still quote the year as the end of a movement,
    # which is a different statement from listing it again.
    assert "the filed accounts report revenue" not in summary["headline"]
    assert "annual result -164k NOK" not in summary["headline"]
    assert "The same filing also reports equity 586k NOK." in summary["headline"]
    # The standalone field is still complete for anything reading the block.
    assert "revenue 2.3m NOK" in summary["financial_line"]
    assert "annual result -164k NOK" in summary["financial_line"]


def test_a_wound_up_company_leads_on_that_and_says_it_once():
    """Adverse status was appended last, behind the figures it invalidates."""
    env = _with_accounts(
        [{"period_to": "2025-12-31", "revenue": 0, "annual_result": -13_000,
          "equity": 66_000, "currency": "NOK"},
         {"period_to": "2023-12-31", "revenue": 0, "annual_result": -8_000,
          "equity": 108_000, "currency": "NOK"}],
        operating_status={"under_liquidation": True, "bankrupt": False})
    summary = build_summary(env)

    assert summary["lead_basis"] == "registry_status"
    assert summary["lead_source"] == "Enhetsregisteret"
    assert summary["headline"].startswith("TESTBEDRIFT AS is flagged in Enhetsregisteret")
    assert summary["headline"].count("under liquidation") == 1


def test_a_small_loss_does_not_displace_a_large_multi_year_move():
    """A 10k loss at a dormant company is a weaker opening than a 65% fall.

    Ranking the loss first put the smaller fact in front on every company that
    filed both.
    """
    env = _with_accounts(
        [{"period_to": "2025-12-31", "annual_result": -10_000, "total_assets": 157_000,
          "equity": 107_000, "currency": "NOK"},
         {"period_to": "2023-12-31", "annual_result": -4_000, "total_assets": 442_000,
          "equity": 117_000, "currency": "NOK"}])
    summary = build_summary(env)

    assert summary["lead_basis"] == "trend_fell"
    assert "total assets falling 64%" in summary["lead"]
    # The loss is one sentence later, not dropped.
    assert "annual result -10k NOK" in summary["headline"]


def test_quoted_registry_text_is_cut_at_a_word_and_closed():
    """A hard slice ended one summary mid-word, with no mark of the cut.

    A description with no full stop of its own also ran straight into the
    sentence after it.
    """
    env = _with_accounts(
        [{"period_to": "2025-12-31", "revenue": 500_000, "annual_result": 1_000,
          "equity": 10_000, "currency": "NOK"},
         {"period_to": "2024-12-31", "revenue": 495_000, "annual_result": 900,
          "equity": 9_000, "currency": "NOK"}],
        stated_purpose="Bidra til langsiktig verdiskapning " + "for sine eiere " * 20,
        website_description={"description": "Strikkebutikk med kvalitetsgarn. Garn og tilbehør"})
    summary = build_summary(env)

    assert "Garn og tilbehør. Its" in summary["headline"] or \
           "Garn og tilbehør. The" in summary["headline"] or \
           "Garn og tilbehør. Across" in summary["headline"]
    quoted = summary["headline"].split("describes it as: ")[1]
    assert not quoted.startswith("Strikkebutikk med kvalitetsgarn. Garn og tilbehørThe")


def test_an_unspecified_industry_is_not_reported_as_a_line_of_business():
    """The register files "uoppgitt" where no industry was given.

    Reading it back produced "an aksjeselskap in uoppgitt", which states a line
    of business the filing explicitly declines to state.
    """
    env = _with_accounts(
        [{"period_to": "2025-12-31", "revenue": 1_000, "annual_result": 1, "equity": 1,
          "currency": "NOK"},
         {"period_to": "2024-12-31", "revenue": 1_000, "annual_result": 1, "equity": 1,
          "currency": "NOK"}],
        legal_identity={"legal_name": "TESTBEDRIFT AS", "legal_form_label": "Aksjeselskap",
                        "industry": [{"label": "Uoppgitt"}], "municipality": "OSLO"})
    summary = build_summary(env)

    assert "uoppgitt" not in summary["headline"].lower()
    assert "It is an aksjeselskap, registered in Oslo." in summary["headline"]


def test_a_note_never_contradicts_the_state_beside_it():
    """A failed request must not be described as an empty register.

    HAUKERØD HAGE OG SKOG AS published a group-structure claim with
    availability `failed` and the note "no official group relationship
    returned". The state said nothing answered; the note said the register
    answered and held nothing. A reader checking an abstention reads the note.
    """
    from signalpost.http import Response
    from signalpost.sources.brreg import _empty_note

    def resp(status, body=b"{}"):
        return Response(url="https://data.brreg.no/x", status=status, body=body)

    empty = "registry lists no subunits for this entity"
    # The register answered and holds nothing: the plain note is the truth.
    assert _empty_note(resp(200), empty) == empty
    assert _empty_note(resp(404), empty) == empty
    # Nothing answered, so absence was never established.
    assert "did not complete" in _empty_note(resp(None), empty)
    assert "not a finding that the record is absent" in _empty_note(resp(None), empty)
    assert "refused" in _empty_note(resp(403), empty)


def _identity_claims(body: dict) -> dict:
    """The identity claims BrregClient derives from one entity response, by field."""
    from signalpost.http import Response
    from signalpost.sources.brreg import BrregClient

    class _Stub:
        requests = 0
        bytes_downloaded = 0

        def fetch(self, url, **kw):  # noqa: ANN001
            return Response(url=url, status=200, body=b"{}", text="{}", final_url=url)

    resp = Response(url="https://data.brreg.no/enhetsregisteret/api/enheter/810034882",
                    status=200, body=b"{}", text="{}",
                    final_url="https://data.brreg.no/enhetsregisteret/api/enheter/810034882")
    claims, _ = BrregClient(_Stub()).identity_claims("810034882", body, resp, "ev-registry")
    return {c.field: c for c in claims}


def test_the_reported_activity_is_published_apart_from_the_statutory_purpose():
    """Two different registry fields, two claims, neither standing in for the other.

    `aktivitet` is what the company reports it does; `vedtektsfestetFormaal` is
    the object drafted into its articles. Collapsing them loses the plain answer
    behind the legal one, which is how the summary came to read as boilerplate.
    """
    from signalpost.envelope import Availability

    claims = _identity_claims({
        "organisasjonsnummer": "810034882",
        "navn": "NJG41 AS",
        "aktivitet": ["Eiendomsutvikling."],
        "vedtektsfestetFormaal": ["Bidra til langsiktig verdiskapning for sine eiere."],
    })
    assert claims["registry_activity"].value == "Eiendomsutvikling."
    assert claims["registry_activity"].availability is Availability.AVAILABLE
    assert claims["registry_activity"].family == "description"
    assert claims["stated_purpose"].value.startswith("Bidra til langsiktig")


def test_an_activity_is_never_published_under_the_statutory_purpose_heading():
    """A company that files an activity but no purpose must not be served the
    activity labelled "statutory purpose" — the label would describe a field the
    value did not come from, which is a wrong claim however true the text is."""
    from signalpost.envelope import Availability

    claims = _identity_claims({
        "organisasjonsnummer": "810034882", "navn": "X AS",
        "aktivitet": ["Eiendomsutvikling."],
    })
    assert claims["stated_purpose"].availability is Availability.NOT_AVAILABLE
    assert claims["stated_purpose"].value is None
    assert claims["registry_activity"].value == "Eiendomsutvikling."


def test_a_missing_audit_opt_out_is_not_published_as_audited():
    """No filed opt-out date is absence of a record, not a finding that the
    accounts were audited. Absence is never a zero."""
    from signalpost.envelope import Availability

    claims = _identity_claims({"organisasjonsnummer": "810034882", "navn": "X AS"})
    audit = claims["audit_exemption"]
    assert audit.availability is Availability.NOT_AVAILABLE
    assert audit.value is None
    assert "no opt-out" in (audit.note or "")

    filed = _identity_claims({"organisasjonsnummer": "810034882", "navn": "X AS",
                              "fravalgRevisjonDato": "2017-07-12"})["audit_exemption"]
    assert filed.availability is Availability.AVAILABLE
    assert filed.value == {"audit_opted_out": True, "opted_out_at": "2017-07-12"}


def test_the_summary_prefers_the_reported_activity_over_the_statutory_boilerplate():
    """Given both, the prose and `what_it_does` carry the concrete activity."""
    from signalpost.envelope import Availability, Claim, Evidence, SourceClass

    env = make_envelope()
    env.evidence.append(Evidence(
        id="ev-registry", source_url="https://data.brreg.no/x",
        source_class=SourceClass.OFFICIAL_REGISTRY, retrieved_at=utcnow(),
        content_sha256="0" * 64, http_status=200))
    for field, family, value in (
        ("legal_identity", "identity", {"legal_name": "NJG41 AS", "municipality": "OSLO"}),
        ("registry_activity", "description", "Eiendomsutvikling."),
        ("stated_purpose", "identity", "Bidra til langsiktig verdiskapning for sine eiere."),
    ):
        env.claims.append(Claim(field=field, family=family, value=value,
                                availability=Availability.AVAILABLE,
                                evidence_ids=["ev-registry"], as_of=utcnow()))
    summary = build_summary(env)
    assert summary["what_it_does"] == "Eiendomsutvikling."
    assert "reports its activity to the register as: Eiendomsutvikling." in summary["headline"]
    assert "Bidra til langsiktig" not in summary["headline"]
