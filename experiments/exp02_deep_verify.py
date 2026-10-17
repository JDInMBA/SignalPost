"""Experiment 02 - does crawling the contact/privacy pages convert 'live but unproven'
candidates into org-number-verified websites?

Norwegian companies are required to state their organisation number on business
documents and web shops, but in practice it most often sits in the footer, on
/kontakt, or in the privacy or terms page rather than in the homepage body.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

sys.path.insert(0, "/Users/jd/Downloads/Case Competition/SignalPost/experiments")
from exp01_domain_discovery import ORG_RE, fetch, mod11_valid, page_org_numbers  # noqa: E402

PATHS = [
    "/kontakt", "/kontakt-oss", "/om-oss", "/om", "/personvern", "/vilkar",
    "/contact", "/about", "/personvernerklaering", "/kjopsvilkar", "/salgsbetingelser",
]
LINK_HINT = re.compile(
    r'href=["\']([^"\']{1,180})["\'][^>]*>(?:[^<]{0,80})'
    r"(kontakt|om\s*oss|personvern|vilk[åa]r|betingelser|about|contact|impressum)",
    re.I,
)


def deep_probe(case: dict) -> dict:
    org = case["org"]
    home = case.get("final_url") or ("https://" + case["domain"])
    base = f"{urlparse(home).scheme}://{urlparse(home).netloc}"
    seen: set[str] = set()
    targets: list[str] = []

    got = fetch(home)
    if got and got[0] == 200 and got[1]:
        for m in LINK_HINT.finditer(got[1]):
            u = urljoin(home, m.group(1).strip())
            if urlparse(u).netloc == urlparse(home).netloc and u not in seen:
                seen.add(u)
                targets.append(u)
    for p in PATHS:
        u = base + p
        if u not in seen:
            seen.add(u)
            targets.append(u)

    for u in targets[:12]:
        res = fetch(u, timeout=6.0)
        if not res or res[0] != 200 or not res[1]:
            continue
        found = page_org_numbers(res[1])
        if org in found:
            return {**case, "status": "verified_deep", "proof_url": res[2], "pages_tried": targets.index(u) + 1}
        other = {o for o in found if mod11_valid(o) and o != org}
        if other:
            return {**case, "status": "other_company_deep", "proof_url": res[2], "other_orgs": sorted(other)[:3]}
    return {**case, "status": "still_unverified", "pages_tried": len(targets[:12])}


def main() -> None:
    src = "/Users/jd/Downloads/Case Competition/SignalPost/experiments/exp01_results.json"
    data = json.load(open(src, encoding="utf-8"))
    cases = [r for r in data["results"] if r["status"] == "live_unverified"]
    print(f"re-probing {len(cases)} 'live but unproven' candidates with contact/privacy crawl", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=12) as pool:
        out = list(pool.map(deep_probe, cases))

    counts: dict[str, int] = {}
    for r in out:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = data["sample"]
    base_verified = data["counts"].get("verified", 0)
    gained = counts.get("verified_deep", 0)
    print("\n=== EXPERIMENT 02: deep verification lift ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<22} {v:>4}")
    print(f"\nhomepage-only verified : {base_verified}/{total} = {100*base_verified/total:.1f}%")
    print(f"deep-crawl added       : +{gained}")
    print(f"combined verified      : {base_verified+gained}/{total} = {100*(base_verified+gained)/total:.1f}%")
    print(f"extra wrong-company caught by the gate: {counts.get('other_company_deep',0)}")
    for r in out:
        if r["status"] == "verified_deep":
            print(f"   + {r['org']}  {r['name'][:34]:<34} -> {r['proof_url']}")
    json.dump(out, open("/Users/jd/Downloads/Case Competition/SignalPost/experiments/exp02_results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
