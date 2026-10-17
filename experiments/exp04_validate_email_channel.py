"""Experiment 04 - validate the registry e-mail domain as a website candidate.

For companies with no registry website but a corporate e-mail domain, fetch the
domain and try to prove the organisation number on the homepage or a small set of
Norwegian contact/terms pages. Compare the verified hit rate against the 4.7%
baseline that name-guessing achieved in experiment 01.
"""
from __future__ import annotations

import gzip
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

sys.path.insert(0, "/Users/jd/Downloads/Case Competition/SignalPost/experiments")
from exp01_domain_discovery import fetch, mod11_valid, page_org_numbers, resolves  # noqa: E402
from exp02_deep_verify import LINK_HINT, PATHS  # noqa: E402
from exp03_email_domain_channel import FREEMAIL, iter_bulk  # noqa: E402

BULK = "/private/tmp/claude-501/-Users-jd-Downloads-CFA-Sch/75cfc810-9cc6-4db1-a38d-6f66792bba26/scratchpad/enheter.json.gz"
UNIVERSE = "/Users/jd/Downloads/signalpost-company-universe-2025.jsonl.gz"


def verify(case: dict) -> dict:
    org, dom = case["org"], case["domain"]
    if not resolves(dom) and not resolves("www." + dom):
        return {**case, "status": "dns_dead"}
    host = dom if resolves(dom) else "www." + dom
    home = None
    for scheme in ("https://", "http://"):
        got = fetch(scheme + host, timeout=8.0)
        if got and got[0] == 200 and got[1]:
            home = got
            break
    if not home:
        return {**case, "status": "no_http"}
    found = page_org_numbers(home[1])
    if org in found:
        return {**case, "status": "verified_home", "proof_url": home[2]}

    base = f"{urlparse(home[2]).scheme}://{urlparse(home[2]).netloc}"
    targets, seen = [], set()
    for m in LINK_HINT.finditer(home[1]):
        u = urljoin(home[2], m.group(1).strip())
        if urlparse(u).netloc == urlparse(home[2]).netloc and u not in seen:
            seen.add(u)
            targets.append(u)
    for p in PATHS:
        u = base + p
        if u not in seen:
            seen.add(u)
            targets.append(u)
    for u in targets[:10]:
        got = fetch(u, timeout=6.0)
        if not got or got[0] != 200 or not got[1]:
            continue
        f2 = page_org_numbers(got[1])
        if org in f2:
            return {**case, "status": "verified_deep", "proof_url": got[2]}
    other = {o for o in found if mod11_valid(o) and o != org}
    if other:
        return {**case, "status": "other_company", "other_orgs": sorted(other)[:3], "proof_url": home[2]}
    return {**case, "status": "live_unproven", "proof_url": home[2]}


def main(n: int = 150, seed: int = 20261013) -> None:
    eligible, had_site = set(), set()
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            eligible.add(r["organisation_number"])
            if (r.get("website") or "").strip():
                had_site.add(r["organisation_number"])
    no_site = eligible - had_site

    cands = []
    for e in iter_bulk(BULK):
        org = e.get("organisasjonsnummer")
        if org not in no_site:
            continue
        email = (e.get("epostadresse") or "").strip().lower()
        if email and "@" in email:
            dom = email.rsplit("@", 1)[-1].strip(" .>")
            if dom and "." in dom and " " not in dom and dom not in FREEMAIL:
                cands.append({"org": org, "name": e.get("navn"), "domain": dom})
    print(f"candidate pool: {len(cands):,}", file=sys.stderr)
    random.Random(seed).shuffle(cands)
    sample = cands[:n]

    with ThreadPoolExecutor(max_workers=16) as pool:
        out = list(pool.map(verify, sample))

    counts: dict[str, int] = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    ver = counts.get("verified_home", 0) + counts.get("verified_deep", 0)
    print("\n=== EXPERIMENT 04: registry e-mail domain as website candidate ===")
    print(f"sample: {len(sample)} companies that have NO registry website")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {v:>4}  ({100*v/len(sample):.1f}%)")
    print(f"\nVERIFIED by org number : {ver}/{len(sample)} = {100*ver/len(sample):.1f}%")
    print(f"  (name-guessing baseline from experiment 01 was 4.7%)")
    print(f"wrong-company rejected : {counts.get('other_company',0)}")
    proj = 39315 * ver / len(sample)
    print(f"\nprojected newly verified websites across the universe: ~{proj:,.0f}")
    print(f"projected website coverage: {100*(44855+proj)/411160:.2f}% (registry alone = 10.91%)")
    for r in out[:40]:
        if r["status"].startswith("verified"):
            print(f"   + {r['org']}  {str(r['name'])[:32]:<32} -> {r['proof_url']}")
    json.dump({"sample": len(sample), "counts": counts, "results": out},
              open("/Users/jd/Downloads/Case Competition/SignalPost/experiments/exp04_results.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
