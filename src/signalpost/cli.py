"""One command the evaluator can paste.

    python -m signalpost run --organisations batch.jsonl --output out/envelopes.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

from . import AGENT_NAME, __version__
from .batch import BatchConfig, run_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalpost",
        description="Evidence-first Norwegian company research agent.")
    parser.add_argument("--version", action="version", version=f"{AGENT_NAME}/{__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="research a batch of organisation numbers")
    run.add_argument("--organisations", required=True,
                     help="JSONL/JSON/plain file of organisation numbers supplied by the evaluator")
    run.add_argument("--output", required=True, help="output JSONL, one envelope per input")
    run.add_argument("--run-id", default=None, help="identifier recorded in every envelope")
    run.add_argument("--report", default=None, help="machine-readable run report")
    run.add_argument("--previous", default=None,
                     help="previous run output, enabling the refresh diff")
    run.add_argument("--workers", type=int, default=6)
    run.add_argument("--expected-count", type=int, default=None)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--timeout", type=float, default=12.0)
    run.add_argument("--per-host-delay", type=float, default=0.4)
    run.add_argument("--max-site-pages", type=int, default=10)
    run.add_argument("--max-candidates", type=int, default=5)
    run.add_argument("--no-jobs", action="store_true", help="disable the Nav hiring connector")
    run.add_argument("--no-website", action="store_true", help="disable website discovery")
    run.add_argument("--nav-index", default="cache/nav_jobs_index.json")
    run.add_argument("--nav-snapshot-pages", type=int, default=40,
                     help="pages of the national active-advertisement list to fetch "
                          "once per run (25 advertisements per page)")
    run.add_argument("--time-budget-seconds", type=float, default=None,
                     help="stop researching new companies after this many seconds; "
                          "remaining inputs still receive a terminal envelope")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        return 2
    config = BatchConfig(
        organisations=args.organisations, output=args.output,
        run_id=args.run_id or f"{AGENT_NAME}-run", report=args.report,
        previous=args.previous, workers=args.workers,
        expected_count=args.expected_count, resume=args.resume,
        timeout=args.timeout, per_host_delay=args.per_host_delay,
        max_site_pages=args.max_site_pages, max_candidates=args.max_candidates,
        enable_jobs=not args.no_jobs, enable_website=not args.no_website,
        nav_index_path=args.nav_index, nav_snapshot_pages=args.nav_snapshot_pages,
        time_budget_seconds=args.time_budget_seconds,
    )
    result = run_batch(config)
    report = result.report
    print(json.dumps({
        "envelopes_written": result.envelopes_written,
        "inputs": report.get("inputs"),
        "one_envelope_per_input": report.get("one_envelope_per_input"),
        "runtime_seconds": report.get("runtime_seconds"),
        "requests": report.get("requests"),
        "third_party_cost_usd": report.get("third_party_cost_usd"),
    }, indent=1))
    return 0 if report.get("one_envelope_per_input") else 1


if __name__ == "__main__":
    sys.exit(main())
