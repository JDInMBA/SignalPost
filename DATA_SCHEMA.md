# Envelope, claims and evidence

## One envelope per input, always

Every organisation number handed to the agent produces exactly one terminal
envelope, in input order. An invalid number, a dead registry, a crashed connector
and a company that genuinely has nothing all produce an envelope — with different
states. A missing row would make the whole run unscorable, so `batch.py` catches
`BaseException` per company and emits a `failed` envelope rather than losing one.

## Availability states

| state | meaning |
|---|---|
| `available` | found, with a value and resolving evidence |
| `not_available` | checked; the source holds nothing |
| `blocked` | the source refused the request (401/403/429, robots) |
| `not_applicable` | the question does not apply to this entity |
| `ambiguous` | something was found but could not be tied to this exact entity |
| `failed` | the attempt broke |

`not_available` is never written as `0`, `""` or `[]`. A company with no
registered employee count carries `value: null`, `availability: not_available`
and a note saying so.

## Envelope

```jsonc
{
  "organisation_number": "810034882",
  "legal_name": "SANDNES ELEKTRISKE AS",
  "status": "available",
  "run": {
    "run_id": "...", "agent_version": "signalpost-agent/1.0.0",
    "started_at": "...", "completed_at": "...",
    "terminal_status": "completed|completed_with_errors|failed",
    "previous_snapshot_at": "..."
  },
  "claims":   [ /* see below */ ],
  "evidence": [ /* see below */ ],
  "changes":  [ /* typed diffs against the previous snapshot */ ],
  "errors":   [ {"stage": "...", "kind": "...", "detail": "...", "retryable": true} ],
  "summary":  { /* decision-useful synthesis, assembled only from claims */ },
  "coverage": { "field": "availability", ... },
  "operations": {
    "requests": 17, "bytes_downloaded": 22832, "runtime_ms": 20564,
    "third_party_cost_usd": 0.0, "strategies_used": ["brreg_entity_v1", ...]
  }
}
```

## Claim

```jsonc
{
  "field": "official_website",
  "family": "website",
  "value": { "url": "...", "domain": "...", "verification": { /* the gate's full reasoning */ } },
  "availability": "available",
  "confidence": 0.88,
  "evidence_ids": ["ev-web-home"],
  "as_of": "2026-10-13T12:00:00Z",
  "reporting_period": "2025-12-31",
  "note": "..."
}
```

`claim_key()` gives a stable identity across runs — organisation number, field and
a digest of the value's natural key — which is what the refresh diff keys on.

## Evidence

```jsonc
{
  "id": "ev-web-home",
  "source_url": "https://firma.no/",
  "final_url": "https://www.firma.no/",
  "redirect_chain": ["https://www.firma.no/"],
  "source_class": "company_owned",
  "http_status": 200,
  "retrieved_at": "2026-10-13T12:00:04Z",
  "content_sha256": "...",
  "claim_span": "org.nr 810 034 882",
  "extraction_method": "static_html",
  "reporting_period": "2025-12-31",
  "licence": "NLOD 2.0",
  "access_policy": "documented open API, no authentication required"
}
```

`validate_integrity()` runs before writing and fails the envelope if an
`available` claim has no value, has no evidence, or points at an evidence id that
does not exist. On the development gold set, evidence validity is **1.000**
across 1,201 available claims.

## Summary

The summary is composed only from claims that already carry evidence. It
introduces no fact, infers no number and softens no unknown: a field that was not
found is named under `unknowns` with the reason it is unknown.

```jsonc
{
  "lead": "The accounts FÆR AS filed with Regnskapsregisteret show equity of -101k NOK for the year to 2025-12-31, on revenue of 2.5m NOK, so its filed liabilities exceed its filed assets.",
  "lead_source": "Regnskapsregisteret",
  "lead_basis": "negative_equity",
  "headline": "<the lead, then the registry description, then the rest>",
  "financial_line": "For the period ending 2025-12-31, the filed accounts report ...",
  "financial_trend": "Across the 2 filed years 2024 to 2025, revenue grew by 112% ...",
  "financial_history": [ /* every filed year, newest first */ ],
  "verified_website": "https://…",            // the site itself was checked
  "registry_declared_website": null,          // filed with the register, host never answered
  "website_confidence": { "level": "high", "basis": "…", "tier": "…", "score": 0.99 },
  "unknowns": [ {"field": "…", "state": "…", "reason": "…"} ]
}
```

### The lead

`lead` is the one fact the summary opens on, and it names the register that holds
it, so the opening claim can be checked without reading the rest of the envelope.
`lead_basis` says which rule chose it. The order is what a reader deciding
whether to deal with this company reads first, not the register's own field
order:

| `lead_basis` | chosen when |
|---|---|
| `registry_status` | the entity register has the company bankrupt or in liquidation |
| `negative_equity` | the latest filed year reports equity below zero |
| `trend_*` | a metric moved 20% or more across the filed years, changed sign, or was filed as nil throughout |
| `loss_making` | the latest filed year reports a loss |
| `latest_year` | nothing above applies; the year's filed figures are stated |
| `open_positions` | no accounts are filed, but the company is advertising |
| `no_accounts` | no accounts are filed, and the reason is given |

A company the register has wound up outranks any figure it ever filed. A balance
sheet that owes more than it owns outranks a growth rate. A growth rate outranks
the size of one year, because one year is a level and the trend is a direction. A
small loss does not displace a large multi-year move.

The prose states each figure once. Whatever the lead already gave is subtracted
from the sentence that follows it, so the summary does not read as a recital of
the registry fields. `financial_line` itself stays whole, because it is a field
of its own that other consumers read.

`verified_website` and `registry_declared_website` are deliberately separate
keys. A site published on the register's word alone has not been checked against
the company, so it must not answer to a key named `verified_`.

## Source classes

`official_registry`, `official_accounts`, `official_public_body`, `company_owned`,
`licensed_api`, `permitted_public_page`, `derived`.

## Families

Families are the unit the evaluation scores coverage on, so each one is populated
independently and reported separately.

Two description fields come from the entity register rather than a website, and
they are not interchangeable with each other or with `stated_purpose`:

- **`stated_purpose`** is `vedtektsfestetFormaal`, the object drafted into the
  articles of association. It is legal drafting and usually reads like it.
- **`registry_activity`** is `aktivitet`, the activity the company itself reports
  to the register. It is concrete, and it is filed for every company in the
  100-company batch, against the 10.91% of the universe that has a website at
  all. For most companies it is the only description that exists.

The summary prefers the company's own website wording, then the reported
activity, then the statutory purpose, and labels whichever it used. A company
filing an activity but no statutory purpose is never served its activity under
the heading "statutory purpose": the heading names the field the value came from.

**`audit_exemption`** is `fravalgRevisjonDato`. A filed date is the register
stating that the accounts above are unaudited. No date is published as
`not_available`, never as "audited" — absence of a record is not a finding.

| family | fields |
|---|---|
| `identity` | `legal_identity`, `operating_status`, `public_brand_aliases`, `stated_purpose` |
| `contact` | `registry_contact`, `website_contact` |
| `workforce` | `registered_employees` |
| `leadership` | `leadership` |
| `locations` | `registered_workplaces` |
| `group` | `group_structure` |
| `financials` | `annual_accounts`, `annual_account_filings`, `audit_exemption` |
| `website` | `official_website` |
| `description` | `registry_activity`, `website_description`, `website_structured_organisation` |
| `social_profiles` | `company_owned_profiles` |
| `dated_activity` | `company_published_activity` |
| `hiring` | `job_postings`, `hiring_signal`, `careers_page` |

The identity, leadership, locations and financials families come from official
registers keyed on the organisation number, so they are available for essentially
the whole universe rather than for the 10.91% of companies that have a registry
website.
