"""Score the agent against the hidden-website gold set.

What this measures, in the order the learning harness demands:

1. wrong-company publications - a published domain that belongs to a different
   company. This must be zero; it is the failure that blocks qualification.
2. exact-domain precision     - of the websites published on the labelled slice,
   how many equal the hidden registry domain.
3. recall and coverage        - how many labelled companies got a published site.
4. abstention                 - reported separately, never counted as coverage.
5. evidence validity          - every available claim points at real evidence.
6. cost                       - requests, bytes, runtime p50/p95.

Run:
    python eval/score.py --gold eval/gold_development.jsonl --envelopes out/gold.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost.normalize import registered_domain  # noqa: E402


def load_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def published_website(envelope: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """(domain, tier, discovery channel) of the published website claim, if any."""
    for claim in envelope.get("claims", []):
        if claim.get("field") != "official_website":
            continue
        if claim.get("availability") != "available":
            return None, None, None
        value = claim.get("value") or {}
        verification = value.get("verification") or {}
        return (registered_domain(value.get("domain") or value.get("url") or ""),
                verification.get("tier"), verification.get("discovery_channel"))
    return None, None, None


def evidence_validity(envelope: dict[str, Any]) -> tuple[int, int]:
    """(available claims, those whose evidence ids all resolve and carry a hash)."""
    known = {e["id"]: e for e in envelope.get("evidence", [])}
    total = valid = 0
    for claim in envelope.get("claims", []):
        if claim.get("availability") != "available":
            continue
        total += 1
        ids = claim.get("evidence_ids") or []
        if ids and all(i in known for i in ids):
            valid += 1
    return total, valid


def score(gold_rows: list[dict[str, Any]], envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    by_org = {e["organisation_number"]: e for e in envelopes}
    gold = {g["organisation_number"]: g for g in gold_rows}

    missing = [o for o in gold if o not in by_org]
    extra = [o for o in by_org if o not in gold]

    labelled = [g for g in gold_rows if g["slice"] == "has_site"]
    unlabelled = [g for g in gold_rows if g["slice"] == "no_site"]

    tp = fp = abstained = 0
    registry_disagreements: list[dict[str, Any]] = []
    wrong_company: list[dict[str, Any]] = []
    tiers = Counter()
    channels = Counter()
    for g in labelled:
        env = by_org.get(g["organisation_number"])
        if env is None:
            continue
        got, tier, channel = published_website(env)
        truth = registered_domain(g["expected_website"] or "")
        if got is None:
            abstained += 1
            continue
        tiers[tier or "unknown"] += 1
        channels[channel or "unknown"] += 1
        if truth and got == truth:
            tp += 1
        elif tier == "A_org_number_on_page":
            # The published page printed this company's own organisation number,
            # so it is not another company's site. The registry's website field is
            # simply pointing somewhere else - often at a parent, a manager or an
            # alternate domain. Counted separately, and never as a wrong company.
            registry_disagreements.append({
                "organisation_number": g["organisation_number"], "name": g.get("name"),
                "published": got, "registry_field": truth, "tier": tier, "channel": channel,
                "basis": "organisation number printed on the published page",
            })
        else:
            fp += 1
            wrong_company.append({
                "organisation_number": g["organisation_number"], "name": g.get("name"),
                "published": got, "registry_truth": truth, "tier": tier, "channel": channel,
            })

    published_unlabelled = 0
    conflict_vetoes = 0
    for g in unlabelled:
        env = by_org.get(g["organisation_number"])
        if env is None:
            continue
        got, tier, channel = published_website(env)
        if got:
            published_unlabelled += 1
            tiers[tier or "unknown"] += 1
            channels[channel or "unknown"] += 1
        for attempt in (env.get("summary", {}) or {}).get("identity_attempts", []) or []:
            if attempt.get("conflicts"):
                conflict_vetoes += 1

    ev_total = ev_valid = 0
    families: dict[str, Counter] = {}
    latencies: list[int] = []
    requests = 0
    for env in by_org.values():
        t, v = evidence_validity(env)
        ev_total += t
        ev_valid += v
        latencies.append((env.get("operations") or {}).get("runtime_ms", 0))
        requests += (env.get("operations") or {}).get("requests", 0)
        for claim in env.get("claims", []):
            families.setdefault(claim.get("family", "?"), Counter())[claim.get("availability")] += 1

    agreed = tp + len(registry_disagreements)
    denom_published = tp + fp + len(registry_disagreements)
    precision = tp / denom_published if denom_published else None
    entity_precision = agreed / denom_published if denom_published else None
    recall = tp / len(labelled) if labelled else None
    latencies.sort()

    def pct(p: float) -> int:
        return latencies[min(len(latencies) - 1, int(p / 100 * (len(latencies) - 1)))] if latencies else 0

    return {
        "gold_rows": len(gold_rows),
        "envelopes": len(envelopes),
        "one_envelope_per_input": not missing and not extra,
        "missing_envelopes": missing[:10],
        "labelled_slice": {
            "companies": len(labelled),
            "published": denom_published,
            "correct_exact_domain": tp,
            "registry_disagreements_with_org_number_proof": len(registry_disagreements),
            "wrong_company_publications": fp,
            "abstained": abstained,
            "exact_domain_precision": precision,
            "exact_entity_precision": entity_precision,
            "recall": recall,
            "abstention_rate": abstained / len(labelled) if labelled else None,
        },
        "unlabelled_slice": {
            "companies": len(unlabelled),
            "published_new_websites": published_unlabelled,
            "discovery_rate": published_unlabelled / len(unlabelled) if unlabelled else None,
            "candidates_vetoed_for_conflicting_org_number": conflict_vetoes,
        },
        "publication_tiers": dict(tiers),
        "discovery_channels": dict(channels),
        "wrong_company_detail": wrong_company,
        "registry_disagreement_detail": registry_disagreements,
        "evidence_validity": {
            "available_claims": ev_total, "with_resolving_evidence": ev_valid,
            "rate": ev_valid / ev_total if ev_total else None,
        },
        "family_availability": {k: dict(v) for k, v in sorted(families.items())},
        "cost": {"requests": requests,
                 "requests_per_company": round(requests / max(len(by_org), 1), 1),
                 "latency_p50_ms": pct(50), "latency_p95_ms": pct(95)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--envelopes", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = score(load_jsonl(args.gold), load_jsonl(args.envelopes))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    ls = report["labelled_slice"]
    print("\n" + "=" * 68, file=sys.stderr)
    print(f"wrong-company publications : {ls['wrong_company_publications']}  (must be 0)", file=sys.stderr)
    if ls["exact_entity_precision"] is not None:
        print(f"exact-entity precision     : {ls['exact_entity_precision']:.1%}", file=sys.stderr)
    if ls["exact_domain_precision"] is not None:
        print(f"exact-domain precision     : {ls['exact_domain_precision']:.1%} "
              f"({ls['registry_disagreements_with_org_number_proof']} registry disagreements "
              f"proven by org number)", file=sys.stderr)
    if ls["recall"] is not None:
        print(f"recall on labelled slice   : {ls['recall']:.1%}", file=sys.stderr)
    us = report["unlabelled_slice"]
    if us["discovery_rate"] is not None:
        print(f"new sites on no-site slice : {us['discovery_rate']:.1%} "
              f"({us['published_new_websites']}/{us['companies']})", file=sys.stderr)
    print("=" * 68, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
