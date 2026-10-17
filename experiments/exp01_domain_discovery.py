"""Experiment 01 - can we recover official websites for companies whose registry row has none?

Method: generate candidate .no/.com domains from the legal name using Norwegian
orthographic rules, fetch each candidate's homepage, and accept only when the page
carries the company's own organisation number (the legally mandated footprint under
foretaksregisterloven ss 10-2). Name similarity alone never accepts.

Reports: verified hit rate, candidates tried per hit, and the wrong-company rate
(accepted pages whose org number does not match).
"""
from __future__ import annotations

import gzip
import json
import random
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UNIVERSE = "/Users/jd/Downloads/signalpost-company-universe-2025.jsonl.gz"
UA = "SignalpostResearch/0.1 (+competition feasibility study; contact submit@builderr.ai)"

LEGAL_SUFFIXES = {
    "as", "asa", "ans", "da", "nuf", "ba", "sa", "enk", "brl", "bbl", "sti",
    "ks", "is", "fli", "esek", "spa", "sam", "iks", "kf", "sf", "pre", "pk",
}
STOPWORDS = {"og", "the", "and"}

TRANSLIT = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "é": "e", "è": "e", "ü": "u", "ä": "a", "ö": "o"})
TRANSLIT_ALT = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e", "è": "e", "ü": "u", "ä": "ae", "ö": "oe"})

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s-]", " ", name.lower(), flags=re.UNICODE)
    parts = [p for p in re.split(r"[\s-]+", cleaned) if p]
    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()
    # drop a branch marker such as "AVD OSLO"
    if "avd" in parts:
        parts = parts[: parts.index("avd")]
    return [p for p in parts if p and p not in STOPWORDS]


def candidate_domains(name: str, limit: int = 8) -> list[str]:
    toks = name_tokens(name)
    if not toks:
        return []
    out: list[str] = []
    for table in (TRANSLIT, TRANSLIT_ALT):
        t = [x.translate(table) for x in toks]
        t = [re.sub(r"[^a-z0-9]", "", x) for x in t]
        t = [x for x in t if x]
        if not t:
            continue
        joined = "".join(t)
        hyphen = "-".join(t)
        for stem in (joined, hyphen, t[0] if len(t[0]) >= 5 else None):
            if not stem or len(stem) < 3 or len(stem) > 63:
                continue
            for tld in (".no", ".com"):
                d = stem + tld
                if d not in out:
                    out.append(d)
    return out[:limit]


ORG_RE = re.compile(r"\b(\d{3})[\s. ]?(\d{3})[\s. ]?(\d{3})\b")


def page_org_numbers(text: str) -> set[str]:
    return {"".join(m.groups()) for m in ORG_RE.finditer(text)}


def mod11_valid(org: str) -> bool:
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    if len(org) != 9 or not org.isdigit():
        return False
    total = sum(int(c) * w for c, w in zip(org[:8], weights))
    rest = total % 11
    check = 0 if rest == 0 else 11 - rest
    return check < 10 and check == int(org[8])


def fetch(url: str, timeout: float = 7.0, cap: int = 400_000) -> tuple[int, str, str] | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            raw = r.read(cap)
            enc = r.headers.get_content_charset() or "utf-8"
            return r.status, raw.decode(enc, "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, "", url
    except Exception:
        return None


def resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def probe(row: dict) -> dict:
    org = row["organisation_number"]
    cands = candidate_domains(row["name"])
    tried = 0
    resolved: list[str] = []
    for d in cands:
        if not resolves(d):
            continue
        resolved.append(d)
        for scheme in ("https://", "http://"):
            tried += 1
            got = fetch(scheme + d)
            if not got or got[0] != 200 or not got[1]:
                continue
            found = page_org_numbers(got[1])
            if org in found:
                return {"org": org, "name": row["name"], "status": "verified",
                        "domain": d, "final_url": got[2], "tried": tried,
                        "resolved": resolved}
            other = {o for o in found if mod11_valid(o) and o != org}
            if other:
                return {"org": org, "name": row["name"], "status": "other_company",
                        "domain": d, "other_orgs": sorted(other)[:3], "tried": tried,
                        "resolved": resolved}
            return {"org": org, "name": row["name"], "status": "live_unverified",
                    "domain": d, "tried": tried, "resolved": resolved}
    return {"org": org, "name": row["name"], "status": "no_candidate_resolved",
            "tried": tried, "candidates": cands, "resolved": resolved}


def main(sample_size: int = 150, seed: int = 20261013) -> None:
    rows = []
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not (r.get("website") or "").strip():
                rows.append(r)
    print(f"companies with NO registry website: {len(rows):,}", file=sys.stderr)
    random.Random(seed).shuffle(rows)
    sample = rows[:sample_size]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(probe, sample))

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\n=== EXPERIMENT 01: website discovery without a registry website ===")
    print(f"sample: {len(sample)} companies (seed {seed})")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<24} {v:>4}  ({100*v/len(sample):.1f}%)")
    verified = [r for r in results if r["status"] == "verified"]
    print(f"\nVERIFIED (org number proven on page): {len(verified)} = {100*len(verified)/len(sample):.1f}%")
    for r in verified[:15]:
        print(f"   {r['org']}  {r['name'][:38]:<38} -> {r['final_url']}")
    wrong = [r for r in results if r["status"] == "other_company"]
    print(f"\nrejected as other company: {len(wrong)} (these would be wrong-company publications without the org-number gate)")
    for r in wrong[:5]:
        print(f"   {r['org']}  {r['name'][:34]:<34} -> {r['domain']} belongs to {r.get('other_orgs')}")
    out = "/Users/jd/Downloads/Case Competition/SignalPost/experiments/exp01_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"seed": seed, "sample": len(sample), "counts": counts, "results": results}, f, ensure_ascii=False, indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
