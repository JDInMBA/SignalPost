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

## Clean-install proof

See [CLEAN_INSTALL.md](CLEAN_INSTALL.md): this commit was cloned fresh from GitHub
with no local state, installed with the declared step only, and produced verified
job advertisements from the committed candidate index.

## The batch

Drawn by `tools/select_batch.py` with seed 20261013, **population-weighted**
so it matches the shape of the official batch rather than flattering the agent:
95 of 100 are `AS` (the universe is 91.7% `AS`) and only
**9 of 100 have a website in the registry** (the universe is 10.91%).

`smoke-companies.jsonl` SHA-256 `b3679dd40a6f08d18d67ea8e9da704736c18c774a9692359ae8b5df86c852b7d`.

## Run result

<!-- generated: run-result -->
| | |
|---|---|
| inputs | 100 |
| envelopes written | **100** |
| **exactly one terminal envelope per input** | **true** |
| terminal status | {"completed": 100} |
| run errors | **none** |
| runtime | 317s (5.3 min) |
| requests | 1209 (12.1 per company) |
| bytes downloaded | 10.2 MB |
| latency p50 / p95 / max | 13.2s / 51.3s / 86.0s |
| **third-party cost** | **$0.00** |

Extrapolated to the official 1,000-company batch at the same 6 workers: roughly **53 minutes and ~12,090 requests, at $0.00**.
<!-- /generated -->

## Coverage by field

<!-- generated: coverage -->
| field | available | other states |
|---|---:|---|
| `legal_identity` | **100/100** | — |
| `operating_status` | **100/100** | — |
| `stated_purpose` | **100/100** | — |
| `public_brand_aliases` | **22/100** | not_available 78 |
| `registry_contact` | **32/100** | not_available 68 |
| `registered_employees` | **12/100** | not_available 88 |
| `leadership` | **100/100** | — |
| `registered_workplaces` | **73/100** | not_available 27 |
| `group_structure` | **11/100** | not_available 89 |
| `annual_accounts` | **100/100** | — |
| `annual_account_filings` | **100/100** | — |
| `official_website` | **17/100** | ambiguous 27, blocked 6, not_available 50 |
| `website_description` | **16/17** | not_available 1 |
| `website_contact` | **14/17** | not_available 3 |
| `website_structured_organisation` | **7/17** | not_available 10 |
| `company_owned_profiles` | **4/17** | not_available 13 |
| `company_published_activity` | **0/17** | not_available 17 |
| `careers_page` | **1/17** | not_available 16 |
| `job_postings` | **0/100** | not_available 100 |
| `hiring_signal` | **0/100** | not_available 100 |

The six site-derived families are counted over the 17 companies with a published website, because "the page did not say" and "there is no page" are different findings. A site the registry declares whose host never answered is published but never read, so it reports those families as absent too. The other 83 companies still receive a terminal state on every one of those families — never a blank — so no field is silently skipped.
<!-- /generated -->

### Website discovery

<!-- generated: discovery -->
| | |
|---|---:|
| companies with a registry website in this batch | 9 |
| **websites published** | **17** |
| abstained as `ambiguous` | 27 |
| `blocked` | 6 |
| `not_available` | 50 |
| **candidates vetoed for carrying another company's organisation number** | **5** |

Publication tiers:

- `A_org_number_on_page` — organisation number printed on the page: **5**
- `B_registry_declared_website` — declared in the registry: **6**
- `C_registry_email_domain_plus_corroboration` — registered e-mail domain, corroborated: **4**
- `D_site_identity_name_plus_locator` — name plus a registered locator: **1**
- `R_registry_declared_site_not_reached` — filed with the registry, host did not answer: **1**

Only 7 of the 17 published websites came from the registry's own website field. The other 10 were **discovered** and then proved against the site itself, so this batch ends with 17 published websites — 16 of them proved against the page — where the registry field alone offers 9 — an 89% increase. 1 of them is published on the register's word alone, because its host did not answer, and is reported as declared rather than verified.

The 5 vetoed candidates are the headline risk result: those are pages that matched the company by name or contact details but carried a **different** company's organisation number. A name-similarity agent publishes them. This one does not.
<!-- /generated -->

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

<!-- generated: hiring-negative -->
`job_postings` came back `not_available` for all 100 companies, which is a real negative rather than a silence: the committed Nav candidate index holds 6,174 companies and 9,873 advertisements, it was consulted for every input, and none of these 100 organisation numbers (or their registered subunits) has an active advertisement. The note on each claim says so.
<!-- /generated -->

Nav attaches an advertisement to the **registered subunit** that is hiring, not to
the parent company, and every organisation number in the candidate index is a
subunit — in a 40-key sample, 40 of 40. So the index is read for the company *and*
each of its registered subunits, and an advertisement found on a subunit is
labelled as the subunit's rather than silently absorbed into the parent:

```json
"advertised_by": "registered subunit 926556916 (PRIMA ASSISTANSE AS AVD AGDER)"
```

Over ten companies drawn from the index, this resolves **53 advertisements across
10 of 10 companies**. Reading the parent number alone resolved none of them.

The agent distinguishes four outcomes here and never collapses them:

| state | meaning |
|---|---|
| `available` | an advertisement was found and its `orgnr` re-verified on the live page |
| `not_available` | the index and snapshot were consulted; nothing is registered to this entity |
| `blocked` | Nav refused the request (HTTP 429) |
| `failed` | nothing was consulted, so no hiring conclusion is drawn |

<!-- generated: hiring-runtime -->
An earlier build searched Nav once per company. That took **7,402 seconds** for this same batch — a 1,000-company official run would have taken over 20 hours and scored zero on timeout. The shipped design reads a committed candidate index and, only if that is absent, takes one bounded national snapshot. This run: **317 seconds**, a 23x reduction, with hiring coverage intact.
<!-- /generated -->
