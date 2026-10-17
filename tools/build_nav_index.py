"""Build the declared organisation-number index of Nav job advertisements.

Why an index exists at all
--------------------------
`arbeidsplassen.nav.no` rate-limits hard (HTTP 429) and caps search pagination,
so querying it once per company does not scale to a 1,000-company batch. Nav's
official change feed is the channel intended for bulk consumption: it returns
1,000 entries per page and carries `employer.orgnr` on each active entry.

What the index is and is not
----------------------------
It is a *candidate* index: organisation number -> advertisement ids. It is never
the evidence for a published claim. At run time every advertisement it nominates
is re-fetched live from the public advertisement page, and the organisation number
is re-read from that live page before anything is published.

The index is declared in SOURCES.md and carries its own build metadata and hash.

Usage
-----
    python tools/build_nav_index.py --out cache/nav_jobs_index.json
    python tools/build_nav_index.py --out cache/nav_jobs_index.json --resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost.envelope import utcnow            # noqa: E402
from signalpost.http import Fetcher               # noqa: E402
from signalpost.sources.nav_jobs import fetch_public_feed_token  # noqa: E402
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

FEED_BASE = "https://pam-stilling-feed.nav.no"
FEED_START = FEED_BASE + "/api/v1/feed"
SEARCH = "https://arbeidsplassen.nav.no/stillinger/api/search?size={size}&from={offset}"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}



def _refresh_token(http: Fetcher, headers: dict[str, str]) -> bool:
    """Nav rotates the public feed token at irregular intervals, so a 401 part way
    through a multi-hour walk means re-acquire it rather than abandon the build."""
    token = fetch_public_feed_token(http)
    if not token:
        return False
    headers["Authorization"] = f"Bearer {token}"
    return True


def walk_feed(
    http: Fetcher,
    headers: dict[str, str],
    *,
    start_url: str,
    max_pages: int,
    verbose: bool,
    checkpoint: Path | None = None,
    if_modified_since: str | None = None,
) -> tuple[dict[str, dict[str, Any]], str | None, int]:
    """Collect the latest known state of every advertisement seen in the feed.

    The initial request can be filtered by date. Subsequent pages follow the
    feed's next_url pagination.
    """
    state: dict[str, dict[str, Any]] = {}
    url: str | None = start_url
    pages = 0
    seen = 0
    last_url: str | None = start_url
    stalls = 0

    while url and pages < max_pages:
        request_headers = headers

        # Apply the date filter only to the first page.
        if pages == 0 and if_modified_since:
            request_headers = {
                **headers,
                "If-Modified-Since": if_modified_since,
            }

        resp = http.fetch(
            url,
            accept="application/json",
            headers=request_headers,
            retries=3,
        )

        if resp.status in {401, 403}:
            if _refresh_token(http, headers):
                if verbose:
                    print(
                        f"  token rotated at page {pages}; re-acquired",
                        file=sys.stderr,
                    )
                continue

            if verbose:
                print(
                    "  token rotated and could not be re-acquired",
                    file=sys.stderr,
                )
            break

        if not resp.ok:
            # Retry transient failures without advancing the feed position.
            stalls += 1

            if stalls > 8:
                if verbose:
                    print(
                        f"  giving up at page {pages}: "
                        f"HTTP {resp.status} {resp.error}",
                        file=sys.stderr,
                    )
                break

            wait = min(5 * stalls, 45)

            if verbose:
                print(
                    f"  page {pages} returned HTTP {resp.status}; "
                    f"retrying in {wait}s (stall {stalls}/8)",
                    file=sys.stderr,
                )

            time.sleep(wait)
            continue

        try:
            page = json.loads(resp.text)
        except (json.JSONDecodeError, TypeError):
            if verbose:
                print(
                    f"  could not decode feed page {pages}",
                    file=sys.stderr,
                )
            break

        stalls = 0
        items = page.get("items") or []

        for item in items:
            entry = item.get("_feed_entry") or {}
            uuid = entry.get("uuid") or item.get("id")

            if not uuid:
                continue

            seen += 1

            # Later feed entries overwrite earlier entries for the same ad.
            state[uuid] = {
                "status": entry.get("status"),
                "title": entry.get("title"),
                "business_name": (
                    entry.get("business_name")
                    or entry.get("businessName")
                ),
                "municipal": entry.get("municipal"),
                "last_changed": entry.get("sistEndret"),
                "entry_id": item.get("id"),
            }

        pages += 1
        last_url = url

        nxt = page.get("next_url")
        url = urljoin(FEED_BASE, nxt) if nxt else None

        if pages % 25 == 0:
            active = sum(
                1 for item in state.values()
                if item["status"] == "ACTIVE"
            )

            if verbose:
                print(
                    f"  page {pages:>5}  seen {seen:>8}  "
                    f"distinct {len(state):>7}  active {active:>6}",
                    file=sys.stderr,
                )

            if checkpoint is not None:
                checkpoint.write_text(
                    json.dumps(
                        {
                            "feed_position": last_url,
                            "pages_walked": pages,
                            "distinct_ads_seen": len(state),
                            "active_ads": active,
                            "active_uuids": [
                                uuid
                                for uuid, item in state.items()
                                if item["status"] == "ACTIVE"
                            ],
                            "updated_at": utcnow(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

        if url is None:
            # Reached the live head of the feed.
            last_url = None

    return state, last_url, pages


def enumerate_active_ads(http: Fetcher, *, limit: int, verbose: bool) -> list[str]:
    """List currently active advertisement ids from the public search endpoint.

    Far cheaper than walking the whole change feed when only active advertisements
    matter: the feed holds every advertisement since 2019, almost all of them long
    since expired. The endpoint caps page size at 25 and caps pagination depth, so
    the walk stops when either limit bites.
    """
    uuids: list[str] = []
    offset = 0
    while len(uuids) < limit:
        resp = http.fetch(SEARCH.format(size=25, offset=offset),
                          accept="application/json", retries=3)
        if not resp.ok:
            if verbose:
                print(f"  search stopped at offset {offset}: HTTP {resp.status}", file=sys.stderr)
            break
        try:
            hits = json.loads(resp.text)["hits"]["hits"]
        except Exception:
            break
        if not hits:
            break
        uuids.extend(h["_id"] for h in hits if h.get("_id"))
        offset += len(hits)
        if verbose and offset % 250 == 0:
            print(f"  enumerated {len(uuids)} active advertisements", file=sys.stderr)
    return uuids[:limit]


def resolve_org_numbers(http: Fetcher, headers: dict[str, str], uuids: list[str],
                        *, verbose: bool, allow_page_fallback: bool = True
                        ) -> dict[str, dict[str, Any]]:
    """Read the advertiser organisation number for each active advertisement.

    The change feed is tried first because it is fast and returns structured
    employer data. It does not carry advertisements syndicated from Finn.no, which
    are the majority of active listings, so those fall back to the public
    advertisement page - slower, because that host rate-limits, but it is the same
    organisation number the runtime verifies against.
    """
    out: dict[str, dict[str, Any]] = {}
    from signalpost.sources.nav_jobs import NavJobsClient  # noqa: PLC0415

    page_client = NavJobsClient(http) if allow_page_fallback else None
    for n, uuid in enumerate(uuids, start=1):
        resp = http.fetch(f"{FEED_BASE}/api/v1/feedentry/{uuid}",
                          accept="application/json", headers=headers, retries=2)
        if resp.status in {401, 403} and _refresh_token(http, headers):
            resp = http.fetch(f"{FEED_BASE}/api/v1/feedentry/{uuid}",
                              accept="application/json", headers=headers, retries=2)
        body: dict[str, Any] = {}
        if resp.ok:
            try:
                body = json.loads(resp.text) or {}
            except Exception:
                body = {}
        ad = body.get("json") or {}
        employer = ad.get("employer") or {}
        orgnr = str(employer.get("orgnr") or "").strip()
        if len(orgnr) == 9 and orgnr.isdigit():
            out[uuid] = {
                "orgnr": orgnr,
                "employer_name": employer.get("name"),
                "employer_homepage": employer.get("homepage"),
                "title": ad.get("jobtitle") or ad.get("title"),
                "published": (ad.get("published") or "")[:10] or None,
                "expires": (ad.get("expires") or "")[:10] or None,
            }
        elif page_client is not None:
            parsed, _ = page_client.verify_ad(uuid)
            if parsed:
                out[uuid] = {
                    "orgnr": parsed["advertiser_organisation_number"],
                    "employer_name": None, "employer_homepage": None,
                    "title": parsed.get("title"),
                    "published": parsed.get("published"),
                    "expires": parsed.get("expires"),
                }
        if verbose and n % 250 == 0:
            print(f"  resolved {n}/{len(uuids)} ({len(out)} with orgnr)", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="cache/nav_jobs_index.json")
    ap.add_argument("--max-pages", type=int, default=6000)
    ap.add_argument( "--since-days", type=int, default=210,help="initial feed lookback window in days (default: 210)",)
    ap.add_argument("--max-active", type=int, default=40000,
                    help="cap on advertisements resolved to an organisation number")
    ap.add_argument("--resume", action="store_true",
                    help="continue from the feed position stored in the existing index")
    ap.add_argument("--from-search", action="store_true",
                    help="enumerate active advertisements from the public search "
                         "endpoint instead of walking the whole change feed")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.since_days < 1:
        ap.error("--since-days must be at least 1")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load(out_path)
    verbose = not args.quiet

    http = Fetcher(timeout=90, per_host_delay=0.25)
    token = fetch_public_feed_token(http)
    if not token:
        print("could not obtain the Nav public feed token", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {token}"}

    start_url = FEED_START
    since_header: str | None = format_datetime(
    datetime.now(timezone.utc) - timedelta(days=args.since_days),
    usegmt=True,
)
    if args.resume and existing.get("feed_position"):
        start_url = existing["feed_position"]
        since_header = None
        if verbose:
            print(f"resuming from {start_url}", file=sys.stderr)

    started = time.monotonic()
    if args.from_search:
        if verbose:
            print("enumerating active advertisements from the public search endpoint ...",
                  file=sys.stderr)
        active = enumerate_active_ads(http, limit=args.max_active, verbose=verbose)
        state, position, pages = {}, existing.get("feed_position"), 0
    else:
        if verbose:
            print("walking the Nav change feed ...", file=sys.stderr)
        state, position, pages = walk_feed(http, headers, start_url=start_url,
                                           max_pages=args.max_pages, verbose=verbose,
                                           checkpoint=out_path.with_suffix(".checkpoint.json"), if_modified_since= since_header)
        active = [u for u, v in state.items() if v.get("status") == "ACTIVE"][: args.max_active]
    if verbose:
        print(f"walked {pages} pages, {len(state)} distinct ads, {len(active)} active",
              file=sys.stderr)
        print("resolving organisation numbers for active advertisements ...", file=sys.stderr)
    resolved = resolve_org_numbers(http, headers, active, verbose=verbose)

    index: dict[str, list[dict[str, Any]]] = {}
    for uuid, meta in resolved.items():
        index.setdefault(meta["orgnr"], []).append({
            "uuid": uuid, "title": meta.get("title"),
            "published": meta.get("published"), "expires": meta.get("expires"),
            "employer_name": meta.get("employer_name"),
        })
    homepages = {m["orgnr"]: m["employer_homepage"]
                 for m in resolved.values() if m.get("employer_homepage")}

    # Merge with any previous build so an incremental run does not lose coverage.
    if args.resume and isinstance(existing.get("index"), dict):
        merged: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in existing["index"].items()}
        for org, ads in index.items():
            known = {a["uuid"] for a in merged.get(org, [])}
            merged.setdefault(org, []).extend(a for a in ads if a["uuid"] not in known)
        index = merged
        homepages = {**(existing.get("employer_homepages") or {}), **homepages}

    payload = {
        "schema": "signalpost.nav_jobs_index/1",
        "built_at": utcnow(),
        "source": ("Nav Arbeidsplassen active-advertisement search + official change feed"
                   if args.from_search else
                   "Nav Arbeidsplassen public change feed (pam-stilling-feed.nav.no/api/v1)"),
        "build_mode": "from_search" if args.from_search else "feed_walk",
        "licence": "Nav Arbeidsplassen API terms (https://arbeidsplassen.nav.no/vilkar-api)",
        "role": "candidate index only; every advertisement is re-fetched and re-verified at run time",
        "feed_position": position,
        "pages_walked": pages,
        "distinct_ads_seen": len(state),
        "active_ads": len(active),
        "ads_with_organisation_number": len(resolved),
        "companies_indexed": len(index),
        "build_seconds": round(time.monotonic() - started, 1),
        "employer_homepages": homepages,
        "index": index,
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    payload["content_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"wrote {out_path}: {len(index):,} companies, {len(resolved):,} advertisements, "
          f"{payload['build_seconds']}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
