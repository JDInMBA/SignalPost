# Connectors, budgets and fallbacks

## Budgets

| control | default | why |
|---|---:|---|
| per-host delay | 0.4s | floor; raised per host below |
| `data.brreg.no` | 0.15s | documented open API built for bulk use |
| `arbeidsplassen.nav.no` | 1.5s | measured: answers HTTP 429 under sustained load |
| `pam-stilling-feed.nav.no` | 0.3s | the channel Nav intends for bulk consumption |
| annual-account copies | 2.1s between request starts | publisher states ~30 starts/minute |
| request timeout | 12s | |
| retries | 1 on timeout / connection refused / DNS / 502 / 503 / 504 | a dropped result costs the whole run |
| pages per company site | 10 | homepage plus the highest-value internal pages |
| website candidates | 5 | ranked; stops at the first that clears the gate |
| body cap | 2MB | |
| workers | 6 | across companies; hosts remain serialised |

## Rate-limit handling

A 429 doubles that host's delay floor (to a 60s ceiling), honours `Retry-After`
when present, and halves the penalty again after each success. The penalty is
per-host, so one noisy source never slows the others.

## Order and fallbacks

```
Enhetsregisteret entity ─┬─ roles
                         ├─ subunits
                         ├─ group structure
                         ├─ annual accounts
                         └─ filing years
          │
          ├─ candidate generation (registry site → e-mail domain → subunit →
          │   job homepage → group parent → name guess)
          │        ↓  per candidate: crawl ≤10 pages → identity gate
          │        ↓  first candidate that clears a tier wins; rest are skipped
          └─ verified site → description, profiles, contact, structured data,
                             dated activity, careers
          │
          └─ Nav jobs: index lookup → live advertisement re-verification
                       (fallback: live search by legal name, aliases, subunit names)
```

Every stage is wrapped. A connector that raises records a `failed` claim and a
run error; it never removes the company from the output.

## JavaScript and PDFs

Static HTML only in this build. No browser is launched, and the annual-account
PDF is published as a linked filing copy rather than parsed. Both are deliberate:
measured against the gold set, neither was the binding constraint on coverage, and
each would add runtime and a heavyweight dependency. `LIMITATIONS.md` records the
conditions under which each would earn its place.

## Adding a connector

1. Give it a name and version in the strategy list it appends to `operations.strategies_used`.
2. Return claims **and** the evidence those claims point at.
3. Bind every claim to the organisation number before returning it.
4. Use the six availability states honestly; never convert absence to a zero.
5. Record licence and access policy on every evidence record.
6. Run it on the development split, then on the zero-overlap validation split.
7. Promote it only through the gate in `EVAL.md`.
