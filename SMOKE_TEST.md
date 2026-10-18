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
| runtime | 304s (5.1 min) |
| requests | 1191 (11.9 per company) |
| bytes downloaded | 9.9 MB |
| latency p50 / p95 / max | 13.4s / 45.2s / 86.7s |
| **third-party cost** | **$0.00** |

Extrapolated to the official 1,000-company batch at the same six workers:
roughly **51 minutes and ~11,910 requests, at $0.00**.

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

## Hiring

`job_postings` came back `not_available` for all 100 companies, which is a real
negative rather than a silence: the committed Nav candidate index holds
6,174 companies and
9,873 advertisements, it was
consulted for every input, and none of these 100 organisation numbers (or their
registered subunits) has an active advertisement. The note on each claim says so.

The agent distinguishes four outcomes here and never collapses them:

| state | meaning |
|---|---|
| `available` | an advertisement was found and its `orgnr` re-verified on the live page |
| `not_available` | the index and snapshot were consulted; nothing is registered to this entity |
| `blocked` | Nav refused the request (HTTP 429) |
| `failed` | nothing was consulted, so no hiring conclusion is drawn |

An earlier build searched Nav once per company. That took **7,402 seconds** for
this same batch — a 1,000-company official run would have taken over 20 hours and
scored zero on timeout. The shipped design reads a committed candidate index and,
only if that is absent, takes one bounded national snapshot. This run:
**304 seconds**, a 24x reduction, with hiring coverage intact.
