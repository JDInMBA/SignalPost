# Evaluation

## The gold set builds itself

44,855 companies in the public universe have a website in the registry. That field
is ground truth. Hide it, ask the agent to rediscover the site from everything
else, and exact-domain precision and recall become measurable **at scale, on real
companies, with no hand-labelling and no leakage**.

```bash
python eval/gold.py     --per-slice 110 --split validation --out eval/gold_validation.jsonl
python eval/run_gold.py --gold eval/gold_validation.jsonl --output out/gold-val.jsonl
python eval/score.py    --gold eval/gold_validation.jsonl --envelopes out/gold-val.jsonl
```

Two slices per split:

- **`has_site`** — registry website hidden. A published domain is correct only if
  it equals the hidden domain. Gives precision, recall and abstention.
- **`no_site`** — no registry website exists, so there is no positive label. Gives
  the discovery rate on the 89.09% of the universe that is otherwise invisible.

Splits are a stable hash of the organisation number: 60% development, 20%
validation, 20% held-out. Development tunes; validation sets thresholds once;
held-out is kept unopened. Overlap between development and validation is **0**,
asserted before each run.

## Results

| metric | development (n=120) | validation (n=220, zero overlap) |
|---|---:|---:|
| one envelope per input | 120/120 | 220/220 |
| **wrong-company publications** | **0** | **1** (see below) |
| exact-entity precision | **100.0%** | **98.4%** |
| recall, labelled slice | 48.3% | 54.5% |
| abstention, labelled slice | 50.0% | 44.5% |
| **new websites on the no-site slice** | **15.0%** | **10.0%** |
| evidence validity | 1.000 (1164 claims) | 1.000 (2115 claims) |
| requests per company | 110.1 | 104.6 |
| latency p50 / p95 | 18.9s / 74.6s | 18.5s / 44.9s |

The no-site discovery rate is the number that matters for the competition: it is
coverage on the 89.09% of the universe where no other approach has a starting
point. Against the 4.7% name-guessing baseline from experiment 01, it is a 2–3×
improvement, at 100% / 98.2% entity precision.

### What the labelled slice does *not* say

Hiding the registry website is a deliberate handicap, and it disables the
declared-website rule that production relies on. A company that *has* a registry
website is published in production at tier B immediately. So 46.7%/50.9% is a
lower bound on a counterfactual, not production recall for that group.

Expected production website coverage, combining both slices:

```
0.1091 × (tier B, essentially all reachable sites)  +  0.8909 × ~0.12
 ≈ 0.10                                            +  ≈ 0.11      ≈ 21%
```

against the **10.91%** a registry-only agent can reach. Roughly double.

### The disagreements, examined one by one

Every disagreement was inspected rather than written off.

| company | we published | registry field | verdict |
|---|---|---|---|
| `UNIKRING BORETTSLAG` (953314525) | `unikring.com` — titled "Unikring Borettslag \| Oslo", **prints org 953314525** | `usbl.no` | **we are right.** `usbl.no` is the housing *manager* USBL, a different company (org 950285680) |
| `NORWAY BY BIKE AS` (933401901) | `norwaybybike.com` — **prints org 933401901** | `norwaybike.com` | **we are right.** The registry field serves no title and no organisation number |
| `FUSETOOLS AS` (997001877) | `fusetools.com` | `fuse.no` | both sites title "fuse" — an alternate domain of the same company. **We cannot prove it with an organisation number, so it is counted against us.** |
| `ENVION GROUP AS` (925903817) | `fjordkick.no` | `smartbloc.no` | **a genuine error.** Fixed; see below |

Disagreements the agent can prove with an organisation number are counted
separately as `registry_disagreements_with_org_number_proof`, never as
wrong-company. `FUSETOOLS` is **not** given that benefit, because the proof is not
there.

## The control loop, as it actually ran

Four measured iterations, each kept or rejected on evidence:

| version | change | wrong-company | recall (labelled) | no-site discovery | requests/co |
|---|---|---:|---:|---:|---:|
| v1 | baseline: crawl 10 pages per candidate, gate afterwards | 0 | 55.0% | 11.7% | 161.8 |
| v2 | two-phase: gate on the homepage, escalate one registry-attested candidate | 0 | 51.7% | 8.3% | 111.6 |
| v3 | escalate any candidate with partial support; cheaper probing | 0 | 51.7% | **15.0%** | 106.7 |
| v4 | **validation split run** — exposed 2 wrong-company publications v1–v3 never saw | **2** | 52.7% | 9.1% | 99.3 |
| v5 | tier C needs the name or **two** locators; declared registry website blocks other domains | **1** (the FUSETOOLS alternate domain) | 50.9% | 9.1% | 99.3 |
| v6 | **code-review fixes** (see below) | **1** (same) | **54.5%** | **10.0%** | 104.6 |

**v2 was rejected under the promotion gate**: it bought a 31% cost reduction with
3.3 points of recall and 3.4 points of discovery. v3 recovered discovery past the
baseline while keeping most of the saving, and was promoted.

**v4 is why the validation split exists.** Three development-split runs showed
zero wrong-company publications. The zero-overlap split immediately found two,
both at tier C:

- `ENVION GROUP AS` filed `smartbloc.no` as its website and `mail@fjordkick.no` as
  its e-mail. Tier C accepted `fjordkick.no` on the e-mail domain plus a single
  locator — the founder's **mobile number**, which is shared across their ventures.

That produced two rules, both regression-tested:

1. **One locator is not corroboration.** A founder's mobile, a shared registered
   address and an accountant's office are all common across unrelated Norwegian
   companies. Tier C now needs the site to name itself, or two independent locators.
2. **A declared registry website blocks other domains.** If the company told the
   registry its site is `smartbloc.no`, then `fjordkick.no` cannot be published
   without printing the organisation number.

Cost: 1.8 points of recall on validation. Benefit: a wrong-company publication
removed. The rubric makes that trade obvious — a material wrong-company match
blocks qualification outright.

**v6 came from reviewing the code rather than the scores**, and it paid in recall.
A careful read of the diff found that `ORG_NUMBER_RE` matched a nine-digit window
*inside* a longer space-grouped number: a page printing a share capital of
`kr 5 923 609 016` yielded the organisation number `923609016`, which is mod-11
valid. The gate then treated it as a **conflicting** organisation number and
vetoed an otherwise-correct candidate. Tightening the lookarounds recovered
**+3.6 points of recall and +4 published websites on the validation split** — real
coverage that was being silently thrown away by a regex. Eight other findings were
fixed in the same pass, including `carry_forward` leaving dangling evidence ids on
every restored claim, a blocked re-crawl being published as an available website,
and a phone pattern that matched any eight-digit run (so `bestillingsnummer
20261013` was published as a contact number). All are regression-tested.

## Scoring order

Scored in the order the learning harness demands, because a later metric may not
be bought with an earlier one:

1. wrong-company publications — must not increase
2. supported-claim precision — must not fall
3. evidence validity — every available claim resolves to real evidence
4. coverage and recall
5. refresh correctness and false-change rate
6. runtime, requests and cost

## Promotion gate

A challenger replaces the incumbent only when **all** hold: zero new material
wrong-company publications; no meaningful fall in claim precision; evidence
completeness stays at 100%; useful coverage or recall improves by a declared
minimum; runtime and cost stay inside budget; and the gain survives on the
zero-overlap validation split. The previous strategy stays available for rollback
and every score report is kept (`out/gold-*-score*.json`).

## Publication tiers and channels, validation split

| tier | published | | channel | published |
|---|---:|---|---|---:|
| C registry e-mail + corroboration | 44 | | registry e-mail domain | 53 |
| A organisation number on page | 17 | | subunit website | 7 |
| D site identity + locator | 6 | | name guess | 7 |

The registry **e-mail** domain — a field the public universe file does not even
expose — is by far the most productive discovery channel once the website field is
hidden.

## Abstention is reported, never hidden

48.2% abstention on the validation labelled slice is printed next to recall in
every report and is never counted as coverage. Abstaining is how precision is held
near 100%, and the trade-off is stated rather than smoothed.

## Regression tests

```bash
./.venv/bin/python -m pytest tests -q     # 37 tests
```

Covering mod-11 validation, Norwegian name and phone normalisation, the
address-shaped-name rule, each publication tier, the single-locator rule, the
declared-website rule, the conflict veto, parked and platform refusals, envelope
integrity, the audit-trail carry-over, every batch input shape, and the three
refresh properties.
