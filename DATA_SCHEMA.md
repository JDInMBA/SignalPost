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

## Source classes

`official_registry`, `official_accounts`, `official_public_body`, `company_owned`,
`licensed_api`, `permitted_public_page`, `derived`.

## Families

Families are the unit the evaluation scores coverage on, so each one is populated
independently and reported separately.

| family | fields |
|---|---|
| `identity` | `legal_identity`, `operating_status`, `public_brand_aliases`, `stated_purpose` |
| `contact` | `registry_contact`, `website_contact` |
| `workforce` | `registered_employees` |
| `leadership` | `leadership` |
| `locations` | `registered_workplaces` |
| `group` | `group_structure` |
| `financials` | `annual_accounts`, `annual_account_filings` |
| `website` | `official_website` |
| `description` | `website_description`, `website_structured_organisation` |
| `social_profiles` | `company_owned_profiles` |
| `dated_activity` | `company_published_activity` |
| `hiring` | `job_postings`, `hiring_signal`, `careers_page` |

The identity, leadership, locations and financials families come from official
registers keyed on the organisation number, so they are available for essentially
the whole universe rather than for the 10.91% of companies that have a registry
website.
