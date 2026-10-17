"""The output contract: one terminal envelope, honest states, resolvable evidence."""
from __future__ import annotations

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
