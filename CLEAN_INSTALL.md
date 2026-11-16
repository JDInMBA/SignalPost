# Clean-install verification

Run against the pinned commit, cloned fresh from GitHub. **No local cache, venv or
generated state was copied in.**

The pinned commit is the one these figures were measured on. A record of a clean
clone cannot sit inside the commit it checked, so the commit submitted is this
one, and the only thing it changes against the pinned commit is the figures on
this page. Everything the agent runs on is identical between the two.

```bash
git clone https://github.com/JDInMBA/SignalPost.git repo && cd repo
git checkout fbb29b94bbc072b3013c1075b9ee5f6fbbad5b37
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
| `python -m signalpost run` (10 companies) | **10/10 envelopes, 57.1s, 93 requests, $0.00** |
| `verify.py` after the runtime install | **30/30** |
| `verify.py` with the declared dev extra | **31/31**, including the explorer payload against the committed envelopes |
| test suite in the clone | **81 passed** |
| `viewer/index.html` opens with no server | ok — the run ships as a classic script, not a fetch |

### Why the test count needs a second install step

The install above is the runtime install, and it is the one the evaluator needs:
it is `--no-deps` against pinned `requirements.txt`, so it does not pull the
`dev` extra, and `pytest` is declared there rather than as a runtime dependency.
A clone that has only run the declared install therefore cannot run the tests,
and `verify.py` reports **30/30** — it counts the suite as a tool it does not
have rather than as a suite that failed, which is the distinction that check
exists to make.

Adding the declared extra is one line, and then the suite runs:

```bash
./.venv/bin/pip install "pytest==8.4.2"   # the version pinned in pyproject.toml [dev]
./.venv/bin/python -m pytest -q           # 81 passed
./.venv/bin/python verify.py              # 31/31
```

Earlier revisions of this page reported the test count without naming that
second step, which read as though the runtime install provided it. It does not.

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

### What those four numbers also show

All four are *subunit* numbers. `GET /enhetsregisteret/api/enheter/{org}` answers
404 for a subunit, so these envelopes carry their advertisements and nothing
else: `legal_identity` is `not_available` and the summary has no name to use as
its subject. The states are honest and no claim is wrong, but the rows are thin.

The agent does not currently fall back to
`/enhetsregisteret/api/underenheter/{org}`, which answers for exactly these
numbers and carries name, address, industry, employee count and the parent's
organisation number. This does not affect the 100-company batch — every input
there resolves as an entity, and `legal_identity` is available for 100 of 100 —
and it is recorded here as a known limit rather than left to be discovered.

## Honest states, verified

A malformed input during this session produced exactly the right behaviour:

```json
{"status": "failed",
 "errors": [{"stage": "input", "kind": "invalid_organisation_number",
             "detail": "not a nine-digit mod-11 valid Norwegian organisation number"}]}
```

A terminal envelope, not a dropped row.
