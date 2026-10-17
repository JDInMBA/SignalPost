"""Build a labelled gold set without hand-labelling a single company.

The idea: 44,855 companies in the public universe have a website in the registry.
That field is ground truth. If we hide it and ask the agent to rediscover the site
from everything else, we can measure exact-domain precision and recall at scale,
on real companies, with no human annotation and no leakage.

Two slices are produced:

  `has_site`   companies with a registry website, hidden during the run. A published
               domain is correct only if it equals the hidden registry domain.
  `no_site`    companies with no registry website. There is no positive label here,
               so this slice measures the abstention rate and, critically, how often
               a candidate carries another company's organisation number.

Splits are assigned by a stable hash of the organisation number so development,
validation and held-out sets never overlap and never move between runs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Iterator

UNIVERSE_DEFAULT = "/Users/jd/Downloads/signalpost-company-universe-2025.jsonl.gz"


def split_of(organisation_number: str) -> str:
    """Stable 60/20/20 split keyed on the organisation number."""
    bucket = int(hashlib.sha256(organisation_number.encode()).hexdigest()[:8], 16) % 100
    if bucket < 60:
        return "development"
    if bucket < 80:
        return "validation"
    return "held_out"


def iter_universe(path: str) -> Iterator[dict[str, Any]]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def build(universe: str, out: str, *, per_slice: int, seed: int, split: str | None) -> None:
    has_site: list[dict[str, Any]] = []
    no_site: list[dict[str, Any]] = []
    for row in iter_universe(universe):
        org = row["organisation_number"]
        if split and split_of(org) != split:
            continue
        record = {
            "organisation_number": org,
            "name": row.get("name"),
            "legal_form": row.get("legal_form"),
            "employees": row.get("employees"),
            "municipality": row.get("municipality"),
            "industry_code": row.get("industry_code"),
            "split": split_of(org),
        }
        site = (row.get("website") or "").strip()
        if site:
            has_site.append({**record, "slice": "has_site", "expected_website": site})
        else:
            no_site.append({**record, "slice": "no_site", "expected_website": None})

    rng = random.Random(seed)
    rng.shuffle(has_site)
    rng.shuffle(no_site)
    sample = has_site[:per_slice] + no_site[:per_slice]
    rng.shuffle(sample)

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in sample:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    meta = {
        "universe": universe, "seed": seed, "split": split or "all",
        "per_slice": per_slice,
        "has_site_population": len(has_site), "no_site_population": len(no_site),
        "rows": len(sample), "content_sha256": digest,
        "labelling": "registry website field, hidden from the agent at run time",
    }
    out_path.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default=UNIVERSE_DEFAULT)
    ap.add_argument("--out", default="eval/gold_companies.jsonl")
    ap.add_argument("--per-slice", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--split", default="development",
                    choices=["development", "validation", "held_out"])
    args = ap.parse_args()
    build(args.universe, args.out, per_slice=args.per_slice, seed=args.seed, split=args.split)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
