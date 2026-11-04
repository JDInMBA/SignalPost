"""Run this in VS Code to check the agent against the challenge requirements.

    python verify.py                 # offline checks only (fast, no network)
    python verify.py --live 5        # also research 5 real companies end to end
    python verify.py --live 5 --refresh   # also prove the refresh is idempotent

Every check prints PASS or FAIL with the evidence it used, and the exit code is
non-zero if any required check fails, so it can be wired into CI.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

GREEN, RED, YEL, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "", required: bool = True) -> bool:
    tag = f"{GREEN}PASS{OFF}" if ok else (f"{RED}FAIL{OFF}" if required else f"{YEL}WARN{OFF}")
    print(f"  [{tag}] {name}")
    if detail:
        print(f"         {DIM}{detail}{OFF}")
    if required:
        results.append((name, ok, detail))
    return ok


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------- offline ---
def check_contract() -> None:
    section("1. Output contract")
    from signalpost.envelope import Availability, Claim, CompanyEnvelope, Evidence, RunMeta, SourceClass, utcnow

    states = {a.value for a in Availability}
    check("six availability states exactly",
          states == {"available", "not_available", "blocked", "not_applicable",
                     "ambiguous", "failed"}, ", ".join(sorted(states)))

    env = CompanyEnvelope(organisation_number="810034882",
                          run=RunMeta(run_id="v", agent_version="t", started_at=utcnow()))
    env.claims.append(Claim(field="x", family="f", value="v",
                            availability=Availability.AVAILABLE, evidence_ids=["missing"]))
    check("an available claim with unresolvable evidence is rejected",
          any("dangling" in p for p in env.validate_integrity()))

    env2 = CompanyEnvelope(organisation_number="810034882",
                           run=RunMeta(run_id="v", agent_version="t", started_at=utcnow()))
    env2.claims.append(Claim(field="employees", family="workforce", value=None,
                             availability=Availability.NOT_AVAILABLE,
                             note="not registered; this is not a count of zero"))
    payload = json.loads(env2.model_dump_json())
    check("absence is null with a reason, never zero",
          payload["claims"][0]["value"] is None and bool(payload["claims"][0]["note"]))


def check_identity() -> None:
    section("2. Exact-entity gate (protects the 30 precision points)")
    from signalpost.identity import Candidate, IdentityGate, Tier

    g = IdentityGate()
    ent = {"organisation_number": "810034882", "legal_name": "SANDNES ELEKTRISKE AS",
           "email": "post@sandneselektriske.no", "business_address": {}}

    def verdict(html, dom="sandneselektriske.no", e=None):
        return g.evaluate(e or ent, Candidate(domain=dom, channel="t",
                                              pages=[(f"https://{dom}/", html)]))

    check("organisation number on the page publishes at the top tier",
          verdict("<title>X</title><footer>Org.nr 810 034 882</footer>").tier is Tier.ORG_NUMBER)
    check("another company's number on the page vetoes publication",
          not verdict("<title>X</title><footer>Orgnr 923 609 016</footer>").accepted)
    check("a parked or for-sale page is refused",
          not verdict("<html><body>Dette domenet er til salgs</body></html>").accepted)
    check("a platform profile is never published as the website",
          not verdict("<title>x</title>", "linkedin.com").accepted)
    addr = {"organisation_number": "991313494", "legal_name": "SKJEBERGVEIEN 25 AS",
            "email": "post@directa.no", "business_address": {}}
    check("an address-shaped company name cannot carry a match",
          not verdict("<title>Directa</title><p>Skjebergveien 25</p>", "directa.no", addr).accepted)


def check_provenance() -> None:
    section("3. Provenance of external items")
    from signalpost.sources.website import (
        CAREER_SEGMENTS, _decode, _handle_belongs_to, _names_the_company, _path_has)

    check("a group site does not lend its name to a subsidiary",
          _names_the_company("<title>Thon Eiendom</title>", "THON SENTER BERGEN AS",
                             "919220252", "thon.no") is False)
    check("the company's own site is recognised",
          _names_the_company("<title>Søvik &amp; Skåravik AS</title>",
                             "SØVIK & SKÅRAVIK AS", "966981911", "sovikskaravik.no") is True)
    check("a chain's social handle is refused",
          not _handle_belongs_to("https://facebook.com/bdekk", "AKERSHUS DEKK AS"))
    check("the company's own handle is accepted",
          _handle_belongs_to("https://facebook.com/sovikskaravik", "SØVIK & SKÅRAVIK AS"))
    check("careers paths match segments, not substrings",
          not _path_has("http://ejobb.no/om-oss", CAREER_SEGMENTS)
          and _path_has("https://x.no/karriere/", CAREER_SEGMENTS))
    check("HTML entities are decoded", _decode("halv&#xe5;rsrapport") == "halvårsrapport")


def check_batch_reader() -> None:
    section("4. Batch input handling")
    from signalpost.batch import read_organisation_numbers

    with tempfile.TemporaryDirectory() as d:
        a = Path(d) / "a.jsonl"
        a.write_text('{"organisation_number":"810034882"}\n{"orgnr":"923609016"}\n')
        b = Path(d) / "b.json"
        b.write_text('[{"organisasjonsnummer":"810034882"},"923609016"]')
        c = Path(d) / "c.txt"
        c.write_text("810034882\n923609016\n810034882\n")
        ok = all(read_organisation_numbers(p) == ["810034882", "923609016"] for p in (a, b, c))
    check("JSONL, JSON array and plain list all accepted, order kept, duplicates dropped", ok)


def check_repo() -> None:
    section("5. Reproducibility")
    req = (ROOT / "requirements.txt").read_text().splitlines()
    pins = [l for l in req if l.strip() and not l.startswith("#")]
    check("every dependency is pinned to an exact version",
          all("==" in l for l in pins), f"{len(pins)} pinned")
    for f in ("README.md", "SUBMISSION.md", "SMOKE_TEST.md", "SOURCES.md", "EVAL.md"):
        check(f"{f} present", (ROOT / f).exists())
    check("the Nav candidate index ships with the repository",
          (ROOT / "cache" / "nav_jobs_index.json").exists(),
          "a clean clone has hiring coverage without a multi-hour build")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT,
                       capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or ["no output"])[-1]
    check("test suite passes", r.returncode == 0, tail)


# ------------------------------------------------------------------- live ---
def check_live(count: int, refresh: bool) -> None:
    section(f"6. Live run over {count} real companies")
    import gzip
    universe = ROOT / "smoke-companies.jsonl"
    orgs = [json.loads(l)["organisation_number"]
            for l in universe.read_text().splitlines()[:count]]
    with tempfile.TemporaryDirectory() as d:
        batch = Path(d) / "batch.jsonl"
        batch.write_text("\n".join(json.dumps({"organisation_number": o}) for o in orgs))
        out, rep = Path(d) / "out.jsonl", Path(d) / "rep.json"
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "signalpost", "run",
                            "--organisations", str(batch), "--output", str(out),
                            "--report", str(rep), "--run-id", "verify",
                            "--expected-count", str(count)],
                           cwd=ROOT, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        if r.returncode != 0:
            check("the run completes", False, r.stderr[-300:])
            return
        rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        report = json.loads(rep.read_text())

        check("exactly one terminal envelope per input",
              len(rows) == count and report["one_envelope_per_input"],
              f"{len(rows)}/{count} in {elapsed:.0f}s, {report['requests']} requests")
        check("input order preserved",
              [e["organisation_number"] for e in rows] == orgs)
        check("third-party cost is zero",
              report["third_party_cost_usd"] == 0.0,
              f"${report['third_party_cost_usd']}")

        states = {c["availability"] for e in rows for c in e["claims"]}
        check("only contract states appear",
              states <= {"available", "not_available", "blocked", "not_applicable",
                         "ambiguous", "failed"}, ", ".join(sorted(states)))

        required = {"legal_identity", "annual_accounts", "annual_account_filings",
                    "leadership", "registered_workplaces", "official_website",
                    "company_owned_profiles", "job_postings"}
        present = {c["field"] for e in rows for c in e["claims"]}
        check("every required envelope section is emitted",
              required <= present, f"missing: {sorted(required - present) or 'none'}")

        bad = []
        for e in rows:
            known = {x["id"] for x in e["evidence"]}
            for c in e["claims"]:
                if c["availability"] == "available":
                    if c["value"] is None or not c["evidence_ids"]:
                        bad.append(f"{e['organisation_number']}:{c['field']}")
                    elif not set(c["evidence_ids"]) <= known:
                        bad.append(f"{e['organisation_number']}:{c['field']} dangling")
        check("every available claim has a value and resolvable evidence",
              not bad, f"{len(bad)} problem(s): {bad[:3]}")

        # A 404 has no body to hash, which is honest. Require the hash only where a
        # claim is actually published on the strength of that retrieval. Evidence ids
        # are scoped to their own envelope, so they must be matched per company.
        backing, hashed = [], []
        for e in rows:
            cited = {eid for c in e["claims"] if c["availability"] == "available"
                     for eid in c["evidence_ids"]}
            for x in e["evidence"]:
                if x["id"] in cited:
                    backing.append(x)
                    if x.get("content_sha256") and x.get("retrieved_at"):
                        hashed.append(x)
        check("every published claim's evidence carries a retrieval time and content hash",
              len(hashed) == len(backing), f"{len(hashed)}/{len(backing)}")

        fab = [f"{e['organisation_number']}" for e in rows for c in e["claims"]
               if c["field"] == "annual_accounts" and c["availability"] == "available"
               for rec in (c["value"] or [])
               if rec.get("revenue") == 0 and rec.get("equity") == 0 and rec.get("total_assets") == 0]
        check("no financial record is fabricated as all-zero", not fab, f"{fab[:3]}")

        check_audit(rows)

        if refresh:
            out2, rep2 = Path(d) / "out2.jsonl", Path(d) / "rep2.json"
            subprocess.run([sys.executable, "-m", "signalpost", "run",
                            "--organisations", str(batch), "--output", str(out2),
                            "--report", str(rep2), "--previous", str(out),
                            "--run-id", "verify-2", "--expected-count", str(count)],
                           cwd=ROOT, capture_output=True, text=True)
            rows2 = [json.loads(l) for l in out2.read_text().splitlines() if l.strip()]
            changed = [e["organisation_number"] for e in rows2 if e["changes"]]
            check("re-running the same snapshot reports no changes",
                  not changed, f"{len(changed)} company/companies reported a change")
            check("the previous snapshot is linked on every envelope",
                  all(e["run"].get("previous_snapshot_at") for e in rows2))


def check_audit(rows: list[dict]) -> None:
    """The specific failures a reviewer called out on our published output.

    Each one is phrased as the reviewer phrased it, so a regression is obvious.
    """
    section("7. Reviewer audit points")
    from signalpost.normalize import registered_domain

    # An output produced before a feature existed cannot demonstrate it. Say so,
    # rather than reporting a failure the current code would not reproduce.
    summaries = [e.get("summary") or {} for e in rows]
    stale = [k for k in ("website_confidence", "financial_trend")
             if not any(k in s for s in summaries)]
    if stale:
        print(f"  {YEL}NOTE{OFF} this output predates {', '.join(stale)}; "
              f"re-run the batch before trusting the last checks")

    site_of = {}
    for e in rows:
        c = next((c for c in e["claims"] if c["field"] == "official_website"), None)
        if c and c["availability"] == "available":
            site_of[e["organisation_number"]] = (c["value"] or {}).get("domain")

    # "link the exact page each item came from"
    unlinked = []
    for e in rows:
        for c in e["claims"]:
            if c["field"] not in {"company_owned_profiles", "company_published_activity",
                                  "careers_page"} or c["availability"] != "available":
                continue
            for item in (c["value"] or []):
                if not isinstance(item, dict) or not item.get("evidence_id"):
                    unlinked.append(f"{e['organisation_number']}:{c['field']}")
    check("every published social, news and careers item links its own page",
          not unlinked, f"{len(unlinked)} unlinked: {unlinked[:3]}")

    # "the page an item came from must be on the company's own site"
    offsite = []
    for e in rows:
        site = site_of.get(e["organisation_number"])
        if not site:
            continue
        known = {x["id"]: x for x in e["evidence"]}
        for c in e["claims"]:
            if c["field"] not in {"company_owned_profiles", "company_published_activity",
                                  "careers_page"} or c["availability"] != "available":
                continue
            for item in (c["value"] or []):
                ev = known.get((item or {}).get("evidence_id"))
                if ev and registered_domain(ev.get("final_url") or ev["source_url"]) != site:
                    offsite.append(f"{e['organisation_number']}:{c['field']}")
    check("no item is sourced from a page outside the company's own site",
          not offsite, f"{len(offsite)}: {offsite[:3]}")

    # "news must be a real dated post"
    undated = [f"{e['organisation_number']}" for e in rows for c in e["claims"]
               if c["field"] == "company_published_activity" and c["availability"] == "available"
               for item in (c["value"] or []) if not item.get("published")]
    check("every published news item carries a publication date",
          not undated, f"{len(undated)}: {undated[:3]}")

    # "the website may be a parent group's - don't lend it its socials/news/jobs"
    leaked = []
    for e in rows:
        c = next((c for c in e["claims"] if c["field"] == "official_website"), None)
        if not c or c["availability"] != "available":
            continue
        if (c["value"] or {}).get("site_names_this_company") is False:
            for f in ("company_owned_profiles", "company_published_activity", "careers_page"):
                d = next((x for x in e["claims"] if x["field"] == f), None)
                if d and d["availability"] == "available":
                    leaked.append(f"{e['organisation_number']}:{f}")
    check("a group or chain site lends this company no profiles, news or jobs",
          not leaked, f"{len(leaked)}: {leaked[:3]}")

    # "check each social link belongs to the company"
    from signalpost.sources.website import _handle_belongs_to
    bad = [f"{e['organisation_number']}:{p['url']}" for e in rows for c in e["claims"]
           if c["field"] == "company_owned_profiles" and c["availability"] == "available"
           for p in (c["value"] or [])
           if not _handle_belongs_to(p["url"], e.get("legal_name") or "")]
    check("every published social handle carries the company's name",
          not bad, f"{len(bad)}: {bad[:3]}")

    # a published website always comes with a stated confidence and its basis
    noconf = [e["organisation_number"] for e in rows
              if e["organisation_number"] in site_of
              and not ((e.get("summary") or {}).get("website_confidence") or {}).get("basis")]
    check("every published website states a confidence level and why",
          not noconf, f"{len(noconf)}: {noconf[:3]}")

    # summaries show more than one year where more than one is filed
    flat = []
    for e in rows:
        acc = next((c for c in e["claims"] if c["field"] == "annual_accounts"), None)
        if acc and acc["availability"] == "available" and len(acc["value"] or []) > 1:
            if not (e.get("summary") or {}).get("financial_trend"):
                flat.append(e["organisation_number"])
    check("a company with several filed years shows a trend, not one year",
          not flat, f"{len(flat)}: {flat[:3]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", type=int, default=0, metavar="N",
                    help="research N real companies over the network")
    ap.add_argument("--refresh", action="store_true",
                    help="run twice and prove the refresh is idempotent")
    ap.add_argument("--audit", metavar="ENVELOPES.jsonl", default=None,
                    help="run the reviewer audit points against an existing output file")
    args = ap.parse_args()

    print("Signalpost — requirement verification")
    print(f"repository: {ROOT}")
    check_contract()
    check_identity()
    check_provenance()
    check_batch_reader()
    check_repo()
    if args.audit:
        rows = [json.loads(l) for l in Path(args.audit).read_text().splitlines() if l.strip()]
        print(f"\n{DIM}auditing {len(rows)} envelopes from {args.audit}{OFF}")
        check_audit(rows)
    if args.live:
        check_live(args.live, args.refresh)
    elif not args.audit:
        print(f"\n{DIM}(skipping the live run; add --live 5 to research real companies){OFF}")

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print(f"{RED}FAILED:{OFF} " + "; ".join(failed))
    else:
        print(f"{GREEN}All required checks passed.{OFF}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
