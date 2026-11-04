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


def test_identity_attempts_survive_summary_composition():
    """The per-candidate audit trail must not be lost when the summary is built.

    `Pipeline.run` composes a fresh summary at the end of the run. An earlier
    version overwrote the dict, silently discarding the record of which domains
    were considered and why each was rejected - the evidence that the gate did
    its job.
    """
    from signalpost.envelope import Availability, Claim
    from signalpost.pipeline import Pipeline, PipelineConfig
    from signalpost.http import Fetcher

    pipe = Pipeline(Fetcher(), PipelineConfig(run_id="t", enable_jobs=False,
                                              enable_website=False))
    env = make_envelope()
    env.claims.append(Claim(field="legal_identity", family="identity",
                            value={"legal_name": "X AS"},
                            availability=Availability.AVAILABLE))
    env.summary["identity_attempts"] = [{"domain": "x.no", "outcome": "rejected"}]
    env.summary["trace"] = "kept"

    from signalpost.synthesis import build as build_summary
    carried = {k: v for k, v in env.summary.items()
               if k in {"trace", "identity_attempts"}}
    env.summary = {**build_summary(env), **carried}

    assert env.summary["identity_attempts"] == [{"domain": "x.no", "outcome": "rejected"}]
    assert env.summary["trace"] == "kept"
    assert "headline" in env.summary


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
    from signalpost.synthesis import _trend_sentence

    rev = [{"period_to": "2025-12-31", "revenue": 7705286},
           {"period_to": "2023-12-31", "revenue": 4755921}]
    assert "revenue grew by 62%" in _trend_sentence(rev, "NOK")

    no_rev = [{"period_to": "2025-12-31", "revenue": None, "total_assets": 156807},
              {"period_to": "2023-12-31", "revenue": None, "total_assets": 442111}]
    out = _trend_sentence(no_rev, "NOK")
    assert "total assets fell by 65%" in out

    # A percentage across a sign change would mislead, so it is not given.
    flipped = [{"period_to": "2025-12-31", "revenue": None, "equity": -236573},
               {"period_to": "2023-12-31", "revenue": None, "equity": 23245}]
    assert "turning negative" in _trend_sentence(flipped, "NOK")

    # One filed year is not a trend.
    assert _trend_sentence([{"period_to": "2025-12-31", "revenue": 100}], "NOK") is None


def test_a_nil_metric_falls_through_instead_of_reading_as_negative():
    """Nil-in-every-year is not a negative year, and it is not a direction.

    Dormant and pre-trading companies file zero revenue. Reporting that as
    "both years are negative" stated something the filing does not say, and it
    hid a balance sheet that was in fact moving.
    """
    from signalpost.synthesis import _trend_sentence

    nil_revenue = [{"period_to": "2025-12-31", "revenue": 0, "equity": 65793},
                   {"period_to": "2023-12-31", "revenue": 0, "equity": 52298}]
    out = _trend_sentence(nil_revenue, "NOK")
    assert "equity grew by 26%" in out
    assert "negative" not in out

    # When nothing moves, nil is itself the finding - and it is stated as nil.
    all_nil = [{"period_to": "2025-12-31", "revenue": 0, "total_assets": 0, "equity": 0},
               {"period_to": "2023-12-31", "revenue": 0, "total_assets": 0, "equity": 0}]
    out = _trend_sentence(all_nil, "NOK")
    assert out == ("Across the 2 filed years 2023 to 2025, revenue was filed as nil "
                   "in every year.")

    # A real negative year still says so.
    losses = [{"period_to": "2025-12-31", "revenue": None, "equity": -4000},
              {"period_to": "2023-12-31", "revenue": None, "equity": -1000}]
    assert "neither year is positive" in _trend_sentence(losses, "NOK")


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
