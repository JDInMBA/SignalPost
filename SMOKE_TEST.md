# 100-company smoke test

Reproduces with the batch committed in this repository.

```bash
./.venv/bin/python -m signalpost run \
  --organisations smoke-companies.jsonl \
  --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json \
  --run-id smoke-100-final \
  --expected-count 100
```

Artifacts are committed under [`results/`](results/): the run report, the refresh
report, the held-out scores and seven complete sample envelopes. The full
`out/*.jsonl` outputs regenerate from the commands here.

## The batch

Drawn by `tools/select_batch.py` with seed 20261013, **population-weighted**
so it matches the shape of the official batch rather than flattering the agent:
95 of 100 are `AS` (the universe is 91.7% `AS`) and only
**9 of 100 have a website in the registry** (the universe is 10.91%).

`smoke-companies.jsonl` SHA-256 `b3679dd40a6f08d18d67ea8e9da704736c18c774a9692359ae8b5df86c852b7d`.

## Run result

| | |
|---|---|
| inputs | 100 |
| envelopes written | **100** |
| **exactly one terminal envelope per input** | **true** |
| terminal status | {"completed": 100} |
| run errors | **none** |
| runtime | 407.4s (6.8 min) |
| requests | 1199 (12.0 per company) |
| bytes downloaded | 9.9 MB |
| latency p50 / p95 / max | 15.3s / 53.8s / 89.1s |
| **third-party cost** | **$0.00** |

Extrapolated to the official 1,000-company batch at the same six workers:
roughly **68 minutes and ~11,990 requests, at $0.00**.

## Coverage by field

| field | available | other states |
|---|---:|---|
| `legal_identity` | **100/100** | — |
| `operating_status` | **100/100** | — |
| `stated_purpose` | **100/100** | — |
| `public_brand_aliases` | **22/100** | not_available 78 |
| `registry_contact` | **32/100** | not_available 68 |
| `registered_employees` | **28/100** | not_available 72 |
| `leadership` | **100/100** | — |
| `registered_workplaces` | **73/100** | not_available 27 |
| `group_structure` | **8/100** | not_available 92 |
| `annual_accounts` | **100/100** | — |
| `annual_account_filings` | **100/100** | — |
| `official_website` | **15/100** | ambiguous 31, blocked 7, not_available 47 |
| `website_description` | **15/15** | — |
| `website_contact` | **14/15** | not_available 1 |
| `website_structured_organisation` | **6/15** | not_available 9 |
| `company_owned_profiles` | **7/15** | not_available 8 |
| `company_published_activity` | **1/15** | not_available 14 |
| `careers_page` | **2/15** | not_available 13 |
| `job_postings` | **0/100** | blocked 100 |
| `hiring_signal` | **0/100** | blocked 100 |

### Website discovery

| | |
|---|---:|
| companies with a registry website in this batch | 9 |
| **websites published** | **15** |
| abstained as `ambiguous` | 31 |
| `blocked` | 7 |
| `not_available` | 47 |
| **candidates vetoed for carrying another company's organisation number** | **4** |

Publication tiers: {"A_org_number_on_page": 5, "D_site_identity_name_plus_locator": 1, "C_registry_email_domain_plus_corroboration": 4, "B_registry_declared_website": 5}

Only 5 of the
15 published websites came from the
registry's own website field. The other
10
were **discovered** and then proved — a 67%
increase over what the registry field alone supports.

The 4 vetoed candidates are the
headline risk result: those are pages that matched the company by name or contact
details but carried a **different** company's organisation number. A
name-similarity agent publishes them. This one does not.

## Idempotent refresh

Re-running the identical batch against the first run as the previous snapshot:

```bash
./.venv/bin/python -m signalpost run --organisations smoke-companies.jsonl \
  --output out/smoke-run2.jsonl --previous out/smoke-run1.jsonl \
  --report out/smoke-run2-report.json --expected-count 100
```

| | |
|---|---|
| envelopes | 100/100 |
| **material changes emitted** | **0** |
| **companies reporting any change** | **0 / 100** |
| errors | none |

Zero false changes. Claim values are compared in a canonical form that sorts lists
and drops volatile keys, so a re-fetched page or a re-ordered board is not a change.

## The hiring family is `blocked` in this run, and that is the honest answer

`job_postings` and `hiring_signal` are `blocked` for all 100 companies. Nav's
public job service returned HTTP 429 to this host for the duration of the run:
sustained development traffic against `arbeidsplassen.nav.no` earned an
IP-level cooldown that outlived the session.

The agent reports that as `blocked` with the reason recorded, rather than as
`not_available`. "The source refused us" and "this company is not hiring" are
different facts, and collapsing them would be exactly the silent-zero failure the
contract forbids.

This also drove a real architectural fix. Searching Nav once per company took more
than 15 minutes for the first 16 companies — a 1,000-company official run would
have taken roughly 15 hours and scored zero on timeout. The agent now fetches the
national active-advertisement list **once per run** and matches employers locally,
so the cost of the hiring family is fixed regardless of batch size. See
`CRAWLERS.md`.
