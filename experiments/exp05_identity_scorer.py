"""Experiment 05 - calibrate a multi-signal exact-entity gate.

The organisation number printed on a page is decisive but rare: experiment 04 showed
56.7% of candidate sites are live yet never print it. The playbook permits "a strong
combination of legal name, address, phone, leadership and independent official
evidence", so this scores several independent signals and measures how coverage and
precision trade off against the threshold.

A conflicting, mod-11-valid organisation number on the page is an absolute veto: that
is the single failure mode that disqualifies a run.
"""
from __future__ import annotations

import gzip
import json
import random
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

sys.path.insert(0, "/Users/jd/Downloads/Case Competition/SignalPost/experiments")
from exp01_domain_discovery import LEGAL_SUFFIXES, candidate_domains, fetch, mod11_valid, page_org_numbers, resolves  # noqa: E402
from exp02_deep_verify import LINK_HINT, PATHS  # noqa: E402
from exp03_email_domain_channel import FREEMAIL, iter_bulk  # noqa: E402

BULK = "/private/tmp/claude-501/-Users-jd-Downloads-CFA-Sch/75cfc810-9cc6-4db1-a38d-6f66792bba26/scratchpad/enheter.json.gz"
UNIVERSE = "/Users/jd/Downloads/signalpost-company-universe-2025.jsonl.gz"
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)

WEIGHTS = {
    "org_number": 1.00,   # decisive on its own
    "registry_website": 0.60,
    "registry_email_domain": 0.55,
    "registry_phone": 0.35,
    "legal_name": 0.30,
    "address": 0.25,
    "postcode_city": 0.20,
}


def norm_text(html: str) -> str:
    txt = TAG_RE.sub(" ", html)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unicodedata.normalize("NFKC", txt).lower()
    return re.sub(r"\s+", " ", txt)


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    parts = [p for p in s.split() if p]
    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    return d[-8:] if len(d) >= 8 else ""


def collect(entity: dict, pages: list[tuple[str, str]]) -> dict:
    org = entity["organisasjonsnummer"]
    blob = " ".join(norm_text(h) for _, h in pages)
    digits = re.sub(r"[^\d]", "", blob)
    signals: dict[str, str] = {}
    conflicts: set[str] = set()

    found = set()
    for _, h in pages:
        found |= page_org_numbers(h)
    if org in found:
        signals["org_number"] = org
    conflicts = {o for o in found if mod11_valid(o) and o != org}

    name = norm_name(entity.get("navn", ""))
    if len(name) >= 5 and name in blob:
        signals["legal_name"] = name

    for key, field in (("registry_phone", "telefon"), ("registry_phone", "mobil")):
        ph = norm_phone(entity.get(field) or "")
        if ph and ph in digits:
            signals[key] = ph

    addr = (entity.get("forretningsadresse") or {})
    street = " ".join(addr.get("adresse") or [])
    sn = norm_name(street)
    if len(sn) >= 6 and sn in blob:
        signals["address"] = street
    pnr, city = addr.get("postnummer"), (addr.get("poststed") or "").lower()
    if pnr and city and pnr in blob and city in blob:
        signals["postcode_city"] = f"{pnr} {city}"

    return {"signals": signals, "conflicts": sorted(conflicts)[:3],
            "score": round(sum(WEIGHTS[k] for k in signals), 3)}


def gather_pages(host: str, max_pages: int = 8) -> list[tuple[str, str]]:
    pages: list[tuple[str, str]] = []
    home = None
    for scheme in ("https://", "http://"):
        got = fetch(scheme + host, timeout=8.0)
        if got and got[0] == 200 and got[1]:
            home = got
            break
    if not home:
        return pages
    pages.append((home[2], home[1]))
    base = f"{urlparse(home[2]).scheme}://{urlparse(home[2]).netloc}"
    targets, seen = [], {home[2]}
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
    for u in targets[: max_pages - 1]:
        got = fetch(u, timeout=6.0)
        if got and got[0] == 200 and got[1]:
            pages.append((got[2], got[1]))
    return pages


def probe(entity: dict) -> dict:
    org = entity["organisasjonsnummer"]
    cands: list[tuple[str, str]] = []
    email = (entity.get("epostadresse") or "").strip().lower()
    if email and "@" in email:
        dom = email.rsplit("@", 1)[-1].strip(" .>")
        if dom and "." in dom and " " not in dom and dom not in FREEMAIL:
            cands.append((dom, "registry_email_domain"))
    for d in candidate_domains(entity.get("navn", "")):
        if d not in [c[0] for c in cands]:
            cands.append((d, "name_guess"))

    best = None
    for dom, channel in cands[:6]:
        host = dom if resolves(dom) else ("www." + dom if resolves("www." + dom) else None)
        if not host:
            continue
        pages = gather_pages(host)
        if not pages:
            continue
        res = collect(entity, pages)
        if channel == "registry_email_domain":
            res["signals"]["registry_email_domain"] = dom
            res["score"] = round(res["score"] + WEIGHTS["registry_email_domain"], 3)
        res.update({"org": org, "name": entity.get("navn"), "domain": dom,
                    "channel": channel, "url": pages[0][0], "pages": len(pages)})
        if best is None or res["score"] > best["score"]:
            best = res
        if res["score"] >= 1.0:
            break
    return best or {"org": org, "name": entity.get("navn"), "score": 0.0,
                    "signals": {}, "conflicts": [], "channel": "none"}


def main(n: int = 200, seed: int = 20261013) -> None:
    eligible, had_site = set(), set()
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            eligible.add(r["organisation_number"])
            if (r.get("website") or "").strip():
                had_site.add(r["organisation_number"])
    no_site = eligible - had_site

    pool = [e for e in iter_bulk(BULK) if e.get("organisasjonsnummer") in no_site]
    print(f"no-site pool loaded: {len(pool):,}", file=sys.stderr)
    random.Random(seed).shuffle(pool)
    sample = pool[:n]

    with ThreadPoolExecutor(max_workers=14) as pool_ex:
        out = list(pool_ex.map(probe, sample))

    print("\n=== EXPERIMENT 05: multi-signal identity gate on companies with NO registry website ===")
    print(f"sample {len(sample)}\n")
    print(f"{'threshold':>9} {'published':>10} {'coverage':>9} {'with conflict (would be WRONG)':>32}")
    for th in (0.30, 0.45, 0.55, 0.60, 0.75, 0.85, 1.00):
        pub = [r for r in out if r["score"] >= th and not r["conflicts"]]
        bad = [r for r in out if r["score"] >= th and r["conflicts"]]
        print(f"{th:>9.2f} {len(pub):>10} {100*len(pub)/len(sample):>8.1f}% {len(bad):>32}")

    print("\nsignal frequency among scored candidates:")
    freq: dict[str, int] = {}
    for r in out:
        for k in r.get("signals", {}):
            freq[k] = freq.get(k, 0) + 1
    for k, v in sorted(freq.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<24} {v}")

    print("\nsample of candidates at score >= 0.55 with no conflict:")
    for r in sorted([x for x in out if x["score"] >= 0.55 and not x["conflicts"]], key=lambda x: -x["score"])[:18]:
        print(f"  {r['score']:.2f} {r['org']} {str(r['name'])[:30]:<30} {r.get('url','')[:46]:<46} {sorted(r['signals'])}")
    json.dump(out, open("/Users/jd/Downloads/Case Competition/SignalPost/experiments/exp05_results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
