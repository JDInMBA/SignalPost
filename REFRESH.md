# Refresh

```bash
python -m signalpost run --organisations batch.jsonl --output out/today.jsonl --previous out/yesterday.jsonl
```

## Three properties, each regression-tested

**1. Idempotence.** Re-running the same source snapshot produces zero changes.
Comparison runs on a canonical form that sorts lists and drops volatile keys
(`retrieved_at`, `as_of`, `confidence`, `evidence_ids`, `verification`), so a
re-ordered board or a re-fetched page is not a change.
→ `tests/test_refresh.py::test_same_snapshot_produces_no_changes`,
`::test_reordering_a_list_is_not_a_change`,
`::test_volatile_fields_do_not_create_false_changes`

**2. Preservation.** A source that is `blocked` or `failed` this run does not erase
what we already hold. `carry_forward()` restores the previous value, caps
confidence at 0.6 and writes a note naming the failure. The failure stays visible
as an `availability_change`.
→ `::test_blocked_source_keeps_the_last_supported_value`

**3. Typed, attributed changes.** List-valued claims diff element by element on a
natural key, so replacing one board member yields `new_role` + `removed_role`, not
a whole-object rewrite.
→ `::test_role_replacement_emits_typed_changes`

## Change types

`new_value`, `changed_value`, `removed_value`, `new_role`, `removed_role`,
`new_job`, `closed_job`, `new_location`, `removed_location`, `new_filing`,
`changed_description`, `new_activity`, `availability_change`.

Each carries `claim_key`, `materiality`, previous and current value, first and
last observed timestamps, and the evidence ids supporting the current side.

## Materiality

Material: identity, operating status, website, leadership, workplaces, accounts,
filings, job postings, company profiles, employees, group structure,
announcements, brand aliases. Everything else is minor. `availability_change` is
minor by design — a source being briefly unreachable is an operational event, not
a business one.

## Volatility

| family | re-check |
|---|---|
| annual accounts, filing years | slowly; they change once a year |
| identity, roles, workplaces, group | daily |
| job postings, dated activity | daily; these are the volatile families |

The agent re-checks everything each run. The table describes which families are
expected to produce changes, which is what the false-change rate is measured
against.
