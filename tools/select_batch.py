"""Draw a local test batch from the public universe.

This exists only for local testing. Builderr supplies the official batch at run
time, and the agent reads organisation numbers from whatever file it is given.

Default is a population-weighted random draw, which is what the official batch
looks like: 91.7% of the eligible universe is AS and 89.09% has no registry
website. `--stratified` instead over-samples rare legal forms and employee bands
to stress the agent on uncommon shapes.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import random
from pathlib import Path

UNIVERSE = "/Users/jd/Downloads/signalpost-company-universe-2025.jsonl.gz"


def stratum(row: dict) -> str:
    employees = row.get("employees")
    if employees is None:
        band = "missing"
    elif employees < 5:
        band = "1-4"
    elif employees < 20:
        band = "5-19"
    elif employees < 100:
        band = "20-99"
    else:
        band = "100+"
    web = "web" if (row.get("website") or "").strip() else "no-web"
    return f"{row.get('legal_form')}|{band}|{web}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default=UNIVERSE)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--output", default="batch.jsonl")
    # Population-weighted by default, because that is what the official batch is:
    # 91.7% of the universe is AS. Stratification is available for stress testing,
    # where rare legal forms and employee bands are deliberately over-sampled.
    ap.add_argument("--stratified", action="store_true", default=False,
                    help="over-sample rare strata for stress testing instead of "
                         "matching the population")
    args = ap.parse_args()

    rows = []
    opener = gzip.open if args.universe.endswith(".gz") else open
    with opener(args.universe, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    rng = random.Random(args.seed)
    if args.stratified:
        buckets: dict[str, list] = collections.defaultdict(list)
        for row in rows:
            buckets[stratum(row)].append(row)
        for bucket in buckets.values():
            rng.shuffle(bucket)
        keys = sorted(buckets)
        rng.shuffle(keys)
        selected, i = [], 0
        while len(selected) < args.count and keys:
            key = keys[i % len(keys)]
            if buckets[key]:
                selected.append(buckets[key].pop())
            else:
                keys.remove(key)
                continue
            i += 1
    else:
        rng.shuffle(rows)
        selected = rows[: args.count]

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in selected:
            fh.write(json.dumps({"organisation_number": row["organisation_number"],
                                 "name": row.get("name")}, ensure_ascii=False) + "\n")

    meta = {
        "universe_rows": len(rows), "selected": len(selected), "seed": args.seed,
        "stratified": args.stratified,
        "content_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "with_registry_website": sum(1 for r in selected if (r.get("website") or "").strip()),
        "legal_forms": dict(collections.Counter(r.get("legal_form") for r in selected)),
    }
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
