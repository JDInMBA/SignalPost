# Identity resolution

A material wrong-company publication is the only failure that keeps a scored run
from becoming official. Everything in this file exists to make that outcome
expensive to reach by accident.

## The join key

The organisation number is the key. Not the name, not the domain, not the brand.
Every connector resolves to it before anything is published:

| source | how it binds to the entity |
|---|---|
| Enhetsregisteret / Regnskapsregisteret | the number *is* the request path |
| Nav job advertisements | the advertisement page carries `orgnr`; subunit numbers resolve to the parent through the registry |
| Company website | the gate below |

The website is the only family where identity has to be *argued*. That is where
the whole risk lives, so that is where the gate is.

## Candidate generation

Ranked by how strongly the registry itself attests the domain. A lower number
wins when two channels nominate the same domain.

| rank | channel | basis |
|---:|---|---|
| 0 | `registry_website` | the company filed this website with the registry |
| 1 | `registry_email_domain` | the company filed `post@domain.no` with the registry |
| 2 | `subunit_website` | a registered workplace of this entity filed it |
| 3 | `job_posting_homepage` | the employer homepage on a verified advertisement |
| 4 | `group_parent_website` | the official group parent's site |
| 5 | `name_guess` | derived from the legal name; 4.7% verified hit rate |

Platform hosts (LinkedIn, Facebook, Instagram, X, YouTube, TikTok, Finn, Proff,
1881, …) are never website candidates. A profile on a platform is a *company-owned
profile* claim, discovered from the verified site, and a different family.

## Signals

Signals are **position-aware**. Where a name appears decides what it means.

| signal | weight in the decision |
|---|---|
| `org_number_on_page` | decisive |
| `registry_declared_website` | decisive |
| `registry_email_domain` | strong, needs corroboration |
| `legal_name_in_site_identity` | strong — `<title>`, `og:site_name`, JSON-LD `Organization.name`, `<footer>`, copyright line |
| `legal_name_in_body` | **not used to publish** |
| `registry_phone` | corroborating locator |
| `registered_street`, `postcode_and_city` | corroborating locator |

`legal_name_in_body` is deliberately inert. A Norwegian page listing partners,
tenants, clients or portfolio companies mentions other companies by name
constantly; `KRØGENES NÆRINGSUTVIKLING AS → miramarecapital.com` in experiment 05
is exactly that, and the body-text rule is what would have published it.

### The address-shaped-name rule

Norwegian property companies are routinely named after their own address:
`SKJEBERGVEIEN 25 AS`, `GAMLE STANGNESVEI 3 AS`, `STORGATA 12 AS`. Any page that
prints that street — a letting agent, a neighbour, a map page — "contains the
legal name". So when a legal name parses as *street word + number*, **name
matching is disabled entirely** for that company, and only the organisation
number or the registry-declared domain can publish it.

## Publication tiers

A candidate publishes only by clearing one named tier.

| tier | condition | confidence |
|---|---|---:|
| **A** `org_number_on_page` | the entity's own organisation number is printed on a crawled page | 0.99 |
| **B** `registry_declared_website` | the candidate domain is the website filed with the registry | 0.95 |
| **C** `registry_email_domain` + corroboration | domain equals the filed e-mail domain **and** the page carries the name in its site identity or a registered locator | 0.88 |
| **D** `name_and_locator` | the site identifies itself by the legal name **and** carries a registered phone, street or postcode+city | 0.80 |

Nothing else publishes. Tier D can be switched off (`tier_d_enabled=False`) if a
future evaluation shows it costing precision.

## The veto

A page carrying a **different** mod-11-valid organisation number rejects the
candidate outright, whatever else matched. The one exception: if the entity's own
number is *also* on the page the conflict is recorded and overridden, because a
group contact page legitimately lists several entities.

Experiment 01 measured this veto firing on **10.7%** of name-matched candidates
and experiment 04 on **14.0%** of e-mail-domain candidates. Those are wrong-company
publications that a name-similarity agent ships and this one does not.

Parked domains, for-sale pages and default server pages are rejected before any
signal is scored.

## Abstention

When no tier is cleared the website claim is published as:

- `ambiguous` — candidates were reached but none proved the entity, or a conflict
  was seen. The note says which;
- `blocked` — every candidate refused the request;
- `not_available` — no candidate resolved to a live site.

Abstention is reported in the run report and in `EVAL.md`. It is never counted as
coverage. Missing a website is a lost point; publishing the wrong one costs the
whole run.

## What the gate is audited against

`eval/` hides the registry website of companies that have one and asks the agent
to rediscover it. A published domain is correct only when it equals the hidden
registry domain, so exact-domain precision, recall and wrong-company rate are all
measurable at scale without hand-labelling. See `EVAL.md`.
