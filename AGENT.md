# Research and abstention policy

## Order of work

1. **Anchor.** Resolve the organisation number in Enhetsregisteret. Legal name,
   form, address, industry, status, capital, former names and statutory purpose
   come from here and ground everything after it.
2. **Official families.** Roles, registered workplaces, group structure,
   normalised annual accounts and the list of filed years. Each is an independent
   request with its own evidence record and its own availability state.
3. **Resolve the public identity.** Generate ranked website candidates, crawl each
   within a page budget, and submit it to the identity gate. Publish at most one.
4. **Harvest the verified site.** Description, company-owned profiles, contact
   points, structured organisation data, dated activity, careers surface. These
   only run on a site that already passed the gate.
5. **External families.** Nav job advertisements, each re-verified against the
   advertiser's organisation number.
6. **Synthesise.** Compose the summary from claims that already carry evidence.
7. **Diff.** Compare with the previous snapshot and emit typed changes.

## Rules

**The organisation number is the only identity.** A brand, domain or handle is a
candidate until tied back to it. Parent, subsidiary, franchise, sister company and
public brand are labelled, never collapsed.

**Search output is a candidate, never evidence.** No search provider is enabled in
this build. If one is added, its result may nominate a URL and nothing more; the
page is independently fetched and must pass the same gate.

**Caches nominate, they never prove.** The Nav advertisement index is a declared
cache of candidate identifiers. Every advertisement it nominates is re-fetched
live and its organisation number re-read from that live page before publication.

**No model decides identity, invents a field or overrides deterministic
evidence.** This build ships no model call at all; the summary is assembled from
claims by template. That is a deliberate choice: it removes fabrication risk,
removes per-run API cost, and makes every sentence traceable.

**Absence is never a zero.** A company with no registered employee count gets
`not_available` with a note saying so, not `0`. A 404 from an official endpoint is
evidence that no record is returned, not evidence of nothing existing.

**A null financial figure stays null.** Nothing in this agent writes a number into
a financial field that the source did not return.

## When to abstain

Abstain — `ambiguous` or `not_available` — when:

- no publication tier is cleared;
- a candidate page carries another company's organisation number;
- the legal name is address-shaped and no number or declared domain is available;
- a source answered, but what it returned cannot be attributed to this entity.

Abstention is recorded with a reason, surfaced in the summary's `unknowns`, and
counted in the run report. It cannot be used to hide low coverage: `EVAL.md`
reports abstention and recall side by side.

## When to carry a value forward

If a source is `blocked` or `failed` this run and the previous snapshot held a
supported value, the value is restored, confidence is capped at 0.6, and the note
records the failure. A transient outage must not look like a company losing its
website or its board.

## Honest reporting

The run report carries requests, bytes, p50/p95 latency, per-field availability,
publication tiers, conflict vetoes, change types, errors and declared caches.
Nothing is smoothed. `LIMITATIONS.md` records the experiments that failed.
