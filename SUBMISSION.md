# Submission

Email to `submit@builderr.ai`.

| item | value |
|---|---|
| Agent name | Signalpost agent |
| Repository | https://github.com/JDInMBA/SignalPost |
| Exact commit hash | the tip of `main` — see *Commit* below |
| Contact | jay.deore2027@sims.edu |
| Clean-clone verification | 100 inputs, 100 unique envelopes, zero errors, 320.5s — see `CLEAN_INSTALL.md` |
| Product UX | `viewer/` — open `python3 -m http.server 8777` in that folder; loads the committed run, or any envelope file via **Open JSONL** |
| Smoke test | `SMOKE_TEST.md` + `results/smoke-report.json` (committed) |

## Commit

Submit **the tip of `main`** of https://github.com/JDInMBA/SignalPost:

```bash
git ls-remote https://github.com/JDInMBA/SignalPost.git refs/heads/main
```

Submit the tip; it is the only version with both the clean-install hiring fix and
the redirect/franchise identity fix. Commit
`12216d5e632884c87df67c82a667037f3b15e01c` and earlier publish a chain portal as
a member company's own website on the validation split and must not be submitted.

The tip contains the code, the pinned dependencies, the committed Nav
candidate index, the 100-company smoke-test report and this file. Earlier commit
`c1b6de58fce6917b8f904aa292cac72a638d0e1c` predates the clean-install hiring fix
and should not be submitted.

## Install step (declared)

```bash
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt && ./.venv/bin/pip install --no-deps -e .
```

## Run command (one line, pasteable)

```bash
./.venv/bin/python -m signalpost run --organisations BATCH.jsonl --output out/envelopes.jsonl --report out/run-report.json --run-id OFFICIAL
```

Add `--previous PREVIOUS.jsonl` to enable the refresh diff, and
`--time-budget-seconds N` to guarantee the run terminates inside a fixed budget
with every remaining input still receiving a terminal envelope.

## Models, APIs, licences

| | |
|---|---|
| Models | **none.** No LLM call at any point. The summary is assembled from claims by template, so nothing can be fabricated and no model key is needed |
| APIs | Brønnøysundregistrene Enhetsregisteret and Regnskapsregisteret (NLOD 2.0, no key); Nav Arbeidsplassen public job service and official change feed (Nav API terms, public rotating token fetched at run time) |
| Other | company websites, crawled under robots.txt |
| Python licences | BSD-3 (beautifulsoup4, extruct), MIT (pydantic, rapidfuzz, tldextract, phonenumbers, pypdf), Apache-2 (trafilatura), BSD (lxml) |

## Expected cost per official batch

**$0.00.** No paid connector, no API key, no model call. `third_party_cost_usd` is
`0.0` in every run report.

## Source rights

See `SOURCES.md`. Every source is an official open-data API, an official public
service whose robots policy permits the access pattern, or a company's own site.
Restricted platforms are not scraped; their URLs appear only when the verified
company site publishes them itself. `w2.brreg.no/kunngjoring/` is declined because
its robots.txt disallows it.

## Secrets

None required. No credential is tied to any personal account, so any run is
reproducible by a third party. If a secret is ever added it is read from a
documented environment variable and never placed in a URL.

## Declared caches

`cache/nav_jobs_index.json` — organisation number → Nav advertisement ids, built
from Nav's official change feed by `tools/build_nav_index.py`. It is a **candidate
index only**: every advertisement it nominates is re-fetched live and its
organisation number re-read from the live page before anything is published. The
file carries `built_at`, `content_sha256` and feed position, and is reported in
every run report under `declared_caches`. The agent runs correctly without it.

## Official-run checklist

| requirement | status |
|---|---|
| exactly one terminal envelope per input | enforced in `batch.py`; `one_envelope_per_input` asserted in the run report and the CLI exits non-zero if it fails |
| honest availability states | six states, `not_available` never written as `0`/`""`/`[]` |
| no fabricated financial value | nulls stay null; accounts sorted newest-first so the latest filed period is the one reported |
| no material wrong-company publication | tiered identity gate with a conflicting-org-number veto; **0** on the development gold set |
| claim-level source, retrieval time, reporting period | every evidence record; `validate_integrity()` rejects a dangling reference before writing |
| idempotent refresh, prior evidence preserved | `refresh.py`, three regression tests |
| reproducible setup, pinned dependencies, one command | `requirements.txt` fully pinned; command above |
| documented source rights and safe URL handling | `SOURCES.md` |
| 100-company smoke test | `SMOKE_TEST.md` |
