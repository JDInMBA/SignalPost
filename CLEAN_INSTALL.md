# Clean-install verification

Run against the pinned commit, cloned fresh from GitHub. **No local cache, venv or
generated state was copied in.**

```bash
git clone https://github.com/JDInMBA/SignalPost.git repo && cd repo
git checkout ca4a3ff87ff342e5b9bfc1c11a0bf17a68b5671a
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install --no-deps -e .
./.venv/bin/python -m signalpost run --organisations smoke-companies.jsonl \
  --output out/envelopes.jsonl --report out/run-report.json --run-id official
```

## Result

| check | outcome |
|---|---|
| clone + checkout at pinned commit | ok |
| Nav candidate index present in the clone | **2,211,813 bytes — 6,174 companies, 9,873 advertisements** |
| declared install step only | ok, no missing dependency |
| `python -m signalpost run` (10 companies) | **10/10 envelopes, 51.8s, 90 requests, $0.00** |
| test suite in the clone | **47 passed** |

## Hiring proved from the fresh clone

Four organisation numbers drawn from the committed index, researched by the clean
clone with nothing else available:

| organisation number | verified advertisements |
|---|---:|
| 915648460 | 12 |
| 974795787 | 12 |
| 976636562 | 1 |
| 980567583 | 8 |

Each advertisement was re-fetched live from `arbeidsplassen.nav.no` and its
`orgnr` re-read from that live page before publication; the index only nominated
the candidate. All 6,174 index keys are mod-11 valid.

This is the check that failed before: with the index gitignored and the live
fallback gated on it, a clean clone produced **no hiring coverage at all**.

## Honest states, verified

A malformed input during this session produced exactly the right behaviour:

```json
{"status": "failed",
 "errors": [{"stage": "input", "kind": "invalid_organisation_number",
             "detail": "not a nine-digit mod-11 valid Norwegian organisation number"}]}
```

A terminal envelope, not a dropped row.
