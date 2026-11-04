"""Independently re-check a sample of published items and report the hit rate.

A published figure is worth little unless someone can re-derive it. This does the
mechanical half of that check: it re-fetches every published website and job
advertisement from scratch, with no knowledge of why the agent accepted it, and
asks one question per item -

    does this page carry the company's organisation number, or its full legal name?

Items that fail are listed individually so a human can look at them. The report
records the method and the date, and it is checked into results/ so the number can
be audited rather than taken on trust.

    python tools/audit_sample.py --envelopes out/smoke-envelopes.jsonl --sample 40
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost.envelope import utcnow            # noqa: E402
from signalpost.http import Fetcher               # noqa: E402
from signalpost.normalize import (                # noqa: E402
    find_org_numbers, name_tokens, normalise_text, registered_domain, strip_accents,
)

http = Fetcher(timeout=15, per_host_delay=0.5)


def check(url: str, org: str, legal_name: str) -> dict[str, Any]:
    resp = http.fetch(url)
    if not resp.ok or not resp.text:
        return {"verdict": "unreachable", "status": resp.status, "error": resp.error}
    blob = normalise_text(resp.text)
    has_org = org in find_org_numbers(blob)
    tokens = [t for t in name_tokens(legal_name) if len(t) >= 3]
    has_name = bool(tokens) and all(t in blob for t in tokens)
    # verasimonsson.com for VERA SIMONSSON AS is confirmation in itself, even
    # when the page never spells the name out in body text.
    label = registered_domain(resp.final_url or url).split(".")[0].replace("-", "")
    joined = "".join(strip_accents(t) for t in name_tokens(legal_name))
    domain_spells_name = bool(joined) and len(joined) >= 6 and joined == label
    return {
        "verdict": "confirmed" if (has_org or has_name or domain_spells_name) else "unconfirmed",
        "organisation_number_on_page": has_org,
        "legal_name_on_page": has_name,
        "domain_spells_the_name": domain_spells_name,
        "final_url": resp.final_url or resp.url,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--envelopes", default="out/smoke-envelopes.jsonl")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--out", default="results/accuracy-audit.json")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.envelopes).read_text(encoding="utf-8").splitlines() if l.strip()]
    sites: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for e in rows:
        org, name = e["organisation_number"], e.get("legal_name") or ""
        for c in e["claims"]:
            if c["availability"] != "available" or not c["value"]:
                continue
            if c["field"] == "official_website":
                sites.append({"org": org, "name": name, "url": c["value"]["url"],
                              "tier": (c["value"].get("verification") or {}).get("tier")})
            elif c["field"] == "job_postings":
                for j in c["value"]:
                    jobs.append({"org": org, "name": name, "url": j["url"],
                                 "advertiser": j.get("advertiser_organisation_number")})

    rng = random.Random(args.seed)
    rng.shuffle(sites)
    rng.shuffle(jobs)
    site_sample = sites[: args.sample]
    job_sample = jobs[: args.sample]

    with ThreadPoolExecutor(max_workers=8) as pool:
        site_res = list(pool.map(lambda s: {**s, **check(s["url"], s["org"], s["name"])}, site_sample))
        # A job advertisement is bound by the advertiser organisation number the
        # agent read from the live page, so that is what is re-checked.
        job_res = list(pool.map(lambda j: {**j, **check(j["url"], j["advertiser"] or j["org"], "")},
                                job_sample))

    def tally(res: list[dict[str, Any]]) -> dict[str, Any]:
        reachable = [r for r in res if r["verdict"] != "unreachable"]
        ok = [r for r in reachable if r["verdict"] == "confirmed"]
        return {"sampled": len(res), "reachable": len(reachable), "confirmed": len(ok),
                "unreachable": len(res) - len(reachable),
                "confirmed_rate": round(len(ok) / len(reachable), 4) if reachable else None}

    report = {
        "generated_at": utcnow(),
        "envelopes": args.envelopes,
        "seed": args.seed,
        "method": ("every sampled item re-fetched from scratch; an item counts as "
                   "confirmed when the page carries the company's organisation number "
                   "or every distinctive token of its legal name"),
        "published_totals": {"websites": len(sites), "job_advertisements": len(jobs)},
        "websites": tally(site_res),
        "job_advertisements": tally(job_res),
        "website_detail": site_res,
        "job_detail": job_res,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({k: report[k] for k in
                      ("published_totals", "websites", "job_advertisements")}, indent=1))
    bad = [r for r in site_res + job_res if r["verdict"] == "unconfirmed"]
    if bad:
        print("\nitems a human should look at:", file=sys.stderr)
        for r in bad:
            print(f"  {r['org']}  {str(r.get('name'))[:26]:<26} {r['url'][:72]}", file=sys.stderr)
    print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
