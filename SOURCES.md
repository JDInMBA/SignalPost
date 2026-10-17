# Sources, licences and access policy

Every source below is an official open-data API, an official public service, or a
company's own website. **No paid connector is enabled and no API key is required**,
so `third_party_cost_usd` is `0.0` on every run.

## Official Norwegian registers

| source | endpoint | licence | access |
|---|---|---|---|
| Enhetsregisteret, entity | `data.brreg.no/enhetsregisteret/api/enheter/{org}` | NLOD 2.0 | documented open API, no key |
| Enhetsregisteret, roles | `…/enheter/{org}/roller` | NLOD 2.0 | documented open API |
| Enhetsregisteret, subunits | `…/underenheter?overordnetEnhet={org}` | NLOD 2.0 | documented open API |
| Group structure | `…/konsernstruktur/{org}` | NLOD 2.0 | documented open API |
| Regnskapsregisteret, accounts | `data.brreg.no/regnskapsregisteret/regnskap/{org}` | NLOD 2.0 | documented open API |
| Annual-account filing years | `…/aarsregnskap/kopi/{org}/aar` | NLOD 2.0 | rate limited to ~30 request starts/minute, enforced in `sources/brreg.py` |

Person data is used only in the company-centric role context. Dates of birth are
never requested, stored or published.

## Nav Arbeidsplassen (public job service)

| surface | endpoint | notes |
|---|---|---|
| advertisement page | `arbeidsplassen.nav.no/stillinger/stilling/{uuid}` | `robots.txt` is `User-agent: * / Disallow:` — the whole site is open to crawlers. Carries the advertiser's `orgnr`, which is what binds a posting to the exact entity |
| search | `arbeidsplassen.nav.no/stillinger/api/search` | documented, unauthenticated. Rate limits hard (HTTP 429); page size caps at 25 and pagination depth is capped |
| change feed | `pam-stilling-feed.nav.no/api/v1/feed` | official bulk channel, 1,000 entries/page, bearer token from Nav's own public endpoint `…/api/publicToken` |

Licence and terms: <https://arbeidsplassen.nav.no/vilkar-api>. The feed token is
public, rotates, and is fetched at run time; it is not a credential tied to any
personal account, so a third party can reproduce any run.

## Company websites

Crawled under `robots.txt`, one request at a time per host, bounded page budget,
identified by a descriptive User-Agent with a contact address. Only a site that
has cleared the identity gate is crawled beyond its homepage.

TLS certificate verification is **off** for company sites. Many small Norwegian
sites have expired or mismatched certificates, and a certificate failure would
silently erase a real company. Content is trusted because the page proves the
organisation number, not because of TLS identity. No credential is ever sent.

## Declared caches

| cache | what it holds | role |
|---|---|---|
| `cache/nav_jobs_index.json` | organisation number → Nav advertisement ids, built by `tools/build_nav_index.py` from the official change feed | **candidate index only.** Every advertisement is re-fetched live and its organisation number re-read from the live page before publication |

The index carries `built_at`, `content_sha256`, feed position and counts, and is
reported in every run report under `declared_caches`. The agent runs correctly
without it, falling back to live search at lower throughput.

## Deliberately not used

| source | why |
|---|---|
| LinkedIn, Meta, Instagram, X, TikTok, Glassdoor, Indeed | access restrictions. Their URLs are published as *company-owned profile* claims when the verified company site links to them, which is the company publishing its own handle. Nothing is scraped from those platforms |
| Norid WHOIS / RDAP | Norid's terms reserve the lookup service and prohibit commercial reuse; the public lookup now returns counts rather than domain lists |
| proff.no, purehelp.no, 1881.no, gulesider.no | commercial directories; terms do not permit the submitted access pattern. `proff.no` additionally geo-blocks non-Norwegian traffic, which would make a run irreproducible |
| Google Places, Brave Search, Serper, Tavily | paid. Implementable behind a declared flag, but they would put a non-zero cost and a key dependency on every official run; the free official ladder was not exhausted first |
| Common Crawl organisation-number reverse index | attractive and lawful, but a ~4-hour offline build per refresh and tens of GB; left as documented future work in `LIMITATIONS.md` |
| Brønnøysund announcements (`w2.brreg.no/kunngjoring/`) | **`robots.txt` disallows `/kunngjoring/`.** This would have been an excellent dated-activity family — official, free and keyed on the organisation number — and the agent declines it. `Fetcher.allowed()` returns `False` for that path and the fetch is recorded as `blocked` with `error="robots_disallowed"` rather than attempted |

## Secrets and URL handling

No secret is required. If one is ever added it is read from a documented
environment variable, never committed, and never placed in a URL. Outbound URLs
are recorded with their full redirect chain and content hash, and no personal or
sensitive value is ever placed in a query string.
