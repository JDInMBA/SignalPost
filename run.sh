#!/usr/bin/env bash
# Convenience wrapper. The evaluator command is the python one-liner in README.md;
# this just fills in the usual defaults.
set -euo pipefail
BATCH="${1:-smoke-companies.jsonl}"
OUT="${2:-out/envelopes.jsonl}"
RUN_ID="${3:-signalpost-$(date -u +%Y%m%dT%H%M%SZ)}"
exec python -m signalpost run \
  --organisations "$BATCH" \
  --output "$OUT" \
  --report "${OUT%.jsonl}-report.json" \
  --run-id "$RUN_ID"
