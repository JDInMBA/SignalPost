"""Run the agent over the gold set with the registry website hidden.

Hiding is done at the identity-gate boundary rather than by editing the registry
response, so every other registry signal (e-mail, phone, address, former names)
stays exactly as it is in production. Only the single labelled field disappears.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost.batch import BatchConfig, run_batch  # noqa: E402
from signalpost import pipeline as pipeline_module   # noqa: E402


def hide_registry_website() -> None:
    """Patch the entity view so the labelled field is invisible to discovery."""
    original = pipeline_module.Pipeline._entity_view

    def patched(self, env):  # noqa: ANN001
        view = original(self, env)
        view["website"] = None
        return view

    pipeline_module.Pipeline._entity_view = patched


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", default="eval/gold_development.jsonl")
    ap.add_argument("--output", default="out/gold-envelopes.jsonl")
    ap.add_argument("--report", default="out/gold-run-report.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-candidates", type=int, default=5)
    ap.add_argument("--keep-website", action="store_true",
                    help="do not hide the registry website (control run)")
    args = ap.parse_args()

    if not args.keep_website:
        hide_registry_website()
        print("[eval] registry website hidden from the agent", file=sys.stderr)

    result = run_batch(BatchConfig(
        organisations=args.gold, output=args.output, run_id="gold-eval",
        report=args.report, workers=args.workers, enable_jobs=False,
        max_candidates=args.max_candidates, timeout=10.0, per_host_delay=0.3,
    ))
    print(json.dumps({"envelopes": result.envelopes_written,
                      "runtime_s": result.report.get("runtime_seconds"),
                      "requests": result.report.get("requests")}, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
