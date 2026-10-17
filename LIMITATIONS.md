# Limitations

## What we measured and did not ship

**Deep-crawling contact and privacy pages to find an organisation number adds
nothing.** Experiment 02 re-probed the 66 candidates that were live but unproven,
crawling `/kontakt`, `/om-oss`, `/personvern`, `/vilkar` and every anchor that
looked like a contact link. It converted **zero**. Those domains are parked or
unrelated, not shy. The deep crawl survives only as phase 2 of website resolution,
where it runs on at most one registry-attested candidate.

**The registry e-mail domain is a strong candidate and a weak proof.** Experiment
03 showed it would lift website coverage from 10.91% to 20.47%. Experiment 04 then
showed that demanding the organisation number on the page verified only 2.0% —
worse than guessing from the name — because 56.7% of those sites never print it.
That is what forced the tiered gate rather than a single rule.

## Known gaps

**Static HTML only.** No browser is launched. A site that renders entirely in
JavaScript yields a homepage with no signals and is abstained on. Adding
Playwright behind a deterministic JavaScript-shell test is the obvious next
strategy; it was not promoted because it was not the binding constraint on the
gold set.

**Annual-account PDFs are linked, not parsed.** The normalised accounts API covers
the figures we publish. Official filing copies are published as links with their
year. Parsing them would add history for entities the normalised endpoint does not
cover — a real gap for parts of the bank and insurance population, where that
endpoint returns source errors.

**No sentiment claim.** No clean, native, human-labelled Norwegian company-news
corpus with usable commercial rights was found, and company-owned marketing copy
is structurally biased. Publishing a sentiment score without a labelled evaluation
would be asserting something we cannot support, so the family abstains.

**No official-announcement family.** Brønnøysund's announcements (bankruptcy,
mergers, capital changes, board changes) are dated public activity for essentially
every company in the universe, keyed on the organisation number, and would have
been one of the strongest recall additions available. `w2.brreg.no/robots.txt`
disallows `/kunngjoring/`, so the agent does not fetch it. Brønnøysund offers an
XML subscription for the same announcements; arranging that is the correct route
and is not something an entrant can do unilaterally mid-competition.

**No reviews or ratings family.** Google Places would supply it, at a per-call
cost and an API key on every official run. The free official ladder was not
exhausted first, so it was not taken.

**Jobs coverage depends on a cache that takes hours to build.**
`arbeidsplassen.nav.no` rate-limits hard, caps page size at 25 and caps pagination
depth, so per-company search does not scale to 1,000 companies. The official
change feed is the right channel, but it holds every advertisement since 2019 and
walking it takes hours. `tools/build_nav_index.py` is resumable, checkpoints every
25 pages, re-acquires the token when Nav rotates it mid-walk, and tolerates the
intermittent 500s the feed returns. Until a full index exists, the connector falls
back to live search, which is correct but narrower.

**Nav's `If-Modified-Since` does not work.** Anchoring the feed to a point in time
returns 500, 502 or 503. Reported here because it is the obvious optimisation and
it is a dead end today.

**Recall on the labelled slice is 55%, abstention 41.7%.** Those 41.7% are
companies whose site exists and was not proven. That is the honest cost of holding
wrong-company publications at zero, and it is the largest remaining recoverable
coverage.

## Considered and rejected

**Common Crawl organisation-number reverse index.** Norwegian companies commonly
print their organisation number on their site, so a reverse index from number to
domain, built offline from Common Crawl and used purely as a candidate generator,
would attack the 89.09% directly. It is lawful and it would be declared like the
Nav index. It is not in this build because the build is roughly four hours and
tens of gigabytes per refresh, and shipping an unvalidated index would have been
worse than shipping none.

**Norid.** The .no registry would be the ideal organisation-number-to-domain join,
and it is closed: Norid's terms reserve the lookup service and prohibit commercial
reuse, and the public lookup now returns counts rather than domain lists.

**Commercial directories.** `proff.no`, `purehelp.no`, `1881.no` and `gulesider.no`
hold exactly this mapping. Their terms do not permit the submitted access pattern.
`proff.no` additionally geo-blocks non-Norwegian traffic, which alone would make a
run irreproducible for an evaluator outside Norway.

**Restricted platforms.** LinkedIn, Meta, Instagram, X, TikTok, Glassdoor and
Indeed are not scraped. Their URLs appear only as company-owned profile claims
when the verified company site links to them — the company publishing its own
handle.

## Operational risks

- **TLS verification is off for company sites.** Many small Norwegian sites have
  expired certificates, and failing them would erase real companies. Identity
  comes from the organisation number on the page, not from the certificate. No
  credential is ever sent to a company site.
- **The Nav public token rotates.** Handled by re-acquiring on 401, but a run
  starting during a rotation may see one blocked request before recovering.
- **Registry disagreements are real.** Two in sixty labelled companies had a
  registry website field pointing at a manager or an alternate domain. Anything
  treating that field as absolute truth inherits those errors.
