# Signalpost agent

An evidence-first research agent for Norwegian companies. Give it organisation
numbers; it returns one terminal envelope per company, with every published fact
bound to the exact legal entity and traceable to a dated, hashed source.

---

## One command

```bash
python -m signalpost run --organisations BATCH.jsonl --output out/envelopes.jsonl --report out/run-report.json
```

Clean-machine setup, start to finish:

```bash
git clone https://github.com/JDInMBA/SignalPost.git signalpost && cd signalpost && python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt && ./.venv/bin/pip install --no-deps -e . && ./.venv/bin/python -m signalpost run --organisations batch.jsonl --output out/envelopes.jsonl --report out/run-report.json
```

`--organisations` accepts whatever shape the batch arrives in: JSONL objects
(`organisation_number`, `organisasjonsnummer`, `orgnr`, …), a JSON array, or a
plain list of numbers, one per line.

Useful flags: `--previous out/yesterday.jsonl` turns on the refresh diff,
`--resume` continues an interrupted run, `--time-budget-seconds N` guarantees the
run ends inside a fixed budget with every remaining input still receiving a
terminal envelope.

## What it costs

No API key. No paid connector. `third_party_cost_usd` is `0.0` for every run.
Every source is an official open-data API or a company's own website.

---

## The problem, measured before it was solved

The public universe holds 411,160 companies. We measured it before writing a
crawler, and the numbers reshaped the design:

| | share of the universe |
|---|---:|
| has a website in the registry | **10.91%** (44,855) |
| has **no** registry website | **89.09%** (366,305) |
| has a registered employee count | 14.6% |
| has a corporate e-mail domain | 17.97% |
| has a statutory purpose on file | **96.76%** |

An agent built around crawling the registry website can therefore reach about a
tenth of the universe. Four experiments (`experiments/`) tested the obvious ways
out, and two of them failed:

| experiment | result |
|---|---|
| 01 guess the domain from the legal name | 4.7% verified; **10.7% of matches belonged to a different company** |
| 02 also crawl `/kontakt`, `/personvern`, … to find the org number | **+0.0pp.** Those domains are parked or unrelated |
| 03 use the registry e-mail domain as a candidate | would lift website coverage 10.91% → 20.47% |
| 04 verify that channel by demanding the org number on the page | only 2.0% — *worse* than guessing |

Experiment 04 is the one that mattered. Demanding a printed organisation number
is high precision and low recall: 56.7% of candidate sites are live but never
print it. The gate had to get richer, not stricter — so it became tiered, and
`experiments/exp05` calibrated the tiers against real failures such as
`SKJEBERGVEIEN 25 AS → directa.no` (an address-shaped company name quoted on an
unrelated page) and `KRØGENES NÆRINGSUTVIKLING AS → miramarecapital.com` (a
portfolio page). Both are rejected by the shipped gate.

## Results

**100-company smoke test** (population-weighted, so 9 of 100 have a registry
website — matching the real universe). Full detail in [SMOKE_TEST.md](SMOKE_TEST.md).

| | |
|---|---|
| exactly one terminal envelope per input | **100/100** |
| run errors | **none** |
| runtime / requests / cost | **5.1 min** · 1,191 requests · **$0.00** |
| websites published | **16** from a batch where only 9 have one in the registry |
| candidates vetoed for carrying another company's organisation number | **4** |
| refresh re-run: companies reporting a change | **0 / 100** |

**Held-out evaluation** — the registry website is hidden and the agent is asked to
rediscover it, giving ground truth at scale with no hand-labelling
([EVAL.md](EVAL.md)):

| | development (n=120) | validation (n=220, zero overlap) |
|---|---:|---:|
| wrong-company publications | **0** | **1** (verified as an alternate domain of the same company) |
| exact-entity precision | **100.0%** | **98.4%** |
| new websites found on companies with *no* registry website | **15.0%** | **10.0%** |
| evidence validity | 1.000 | 1.000 |

Against the 4.7% name-guessing baseline, discovery on the invisible 89% is a 2–3×
improvement at ~100% entity precision.

## How it scores

The evaluation weights recall at 50 and precision at 30, and a single material
wrong-company publication stops a run becoming official. So the agent pursues
recall through **breadth across families with an exact join key**, not through
looser matching:

- **the organisation number is the join key**, not the company name. Every source
  here is keyed on it, so exact-entity precision is a property of the data model
  rather than of a similarity threshold;
- **registry families are complete**: identity, status, brand aliases, statutory
  purpose, contact, employees, leadership, workplaces, group, accounts and filing
  history — available for essentially the whole universe, not the 10.91% with a
  website. In the smoke test, identity, leadership, accounts and statutory purpose
  came back for **100 of 100** companies;
- **the website is discovered, not assumed**, through ranked channels, each
  published only behind the identity gate;
- **hiring comes from Nav**, whose advertisement pages carry the advertiser's
  organisation number, and whose advertisements attach to registered subunits
  that this agent resolves back to the parent and labels rather than merges;
- **absence is a state, never a zero.**

## Documentation

| file | what it covers |
|---|---|
| [AGENT.md](AGENT.md) | research and abstention policy |
| [IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md) | the four publication tiers and the veto |
| [CRAWLERS.md](CRAWLERS.md) | connectors, budgets, rate limits and fallbacks |
| [DATA_SCHEMA.md](DATA_SCHEMA.md) | envelope, claims, evidence and families |
| [REFRESH.md](REFRESH.md) | snapshots, diffs and idempotence |
| [EVAL.md](EVAL.md) | the gold set, metrics, the four measured iterations and the promotion gate |
| [SMOKE_TEST.md](SMOKE_TEST.md) | the 100-company result and how to reproduce it |
| [CLEAN_INSTALL.md](CLEAN_INSTALL.md) | fresh-clone verification at the pinned commit |
| [viewer/README.md](viewer/README.md) | the profile viewer: find, compare and verify on desktop and mobile |
| [SUBMISSION.md](SUBMISSION.md) | submission checklist, declared models, APIs and cost |
| [SOURCES.md](SOURCES.md) | every source, its licence and its access policy |
| [LIMITATIONS.md](LIMITATIONS.md) | what this does not do, and what we got wrong |

## Viewing the output

```bash
cd viewer && python3 -m http.server 8777     # open http://localhost:8777
```

Every claim renders with its availability state, its value, and its evidence —
source link, retrieval time and content hash — so any single fact can be checked
back to where it came from. Filters for verified website, hiring, abstained and
adverse status; works at phone width. See [viewer/README.md](viewer/README.md).

## Repository layout

```
src/signalpost/
  normalize.py     Norwegian names, phones, domains, mod-11 organisation numbers
  envelope.py      typed envelope, claims, evidence, availability states
  http.py          robots-aware fetcher: throttling, 429 backoff, provenance
  identity.py      the exact-entity gate
  discovery.py     ranked website candidate generation
  pipeline.py      one company, end to end
  batch.py         one envelope per input, always
  refresh.py       typed diffs, idempotence, carry-forward
  synthesis.py     supported-only summary
  sources/         brreg.py, website.py, nav_jobs.py
viewer/            dependency-free profile viewer (desktop and mobile)
eval/              gold-set builder, runner and scorer
experiments/       the measurements that shaped the design
tools/             declared-cache builders
tests/             contract, gate and refresh regressions
```

## Tests

```bash
./.venv/bin/python -m pytest tests -q
```

## Smoke test

[SMOKE_TEST.md](SMOKE_TEST.md) holds the 100-company result, the refresh
idempotence check and the exact commands that produced them.
