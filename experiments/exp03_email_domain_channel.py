"""Experiment 03 - how much website coverage does the registry's own e-mail field unlock?

The public universe file exposes only `website`. The full Enhetsregisteret bulk export
also carries `epostadresse`, `telefon`, `historiskeNavn` and `vedtektsfestetFormaal`.
A corporate e-mail domain (post@firma.no) is a registry-attested link from the
organisation number to a domain, which is far stronger than guessing from the name.
"""
from __future__ import annotations

import collections
import gzip
import json
import re
import sys

BULK = "/private/tmp/claude-501/-Users-jd-Downloads-CFA-Sch/75cfc810-9cc6-4db1-a38d-6f66792bba26/scratchpad/enheter.json.gz"
UNIVERSE = "/Users/jd/Downloads/signalpost-company-universe-2025.jsonl.gz"

FREEMAIL = {
    "gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "yahoo.com",
    "yahoo.no", "live.no", "live.com", "online.no", "icloud.com", "me.com", "msn.com",
    "broadpark.no", "start.no", "c2i.net", "frisurf.no", "getmail.no", "lyse.net",
    "hotmail.co.uk", "proton.me", "protonmail.com", "mac.com", "telia.com", "altibox.no",
    "yahoo.se", "aol.com", "mail.com", "homenet.no", "sf-nett.no", "epost.no",
}


def iter_bulk(path: str):
    dec = json.JSONDecoder()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        buf = f.read(8_000_000)
        i = buf.index("[") + 1
        while True:
            while i < len(buf) and buf[i] in " \n\r\t,":
                i += 1
            if i < len(buf) and buf[i] == "]":
                return
            try:
                obj, j = dec.raw_decode(buf, i)
                yield obj
                i = j
            except ValueError:
                more = f.read(4_000_000)
                if not more:
                    return
                buf = buf[i:] + more
                i = 0


def main() -> None:
    eligible = set()
    had_site = set()
    with gzip.open(UNIVERSE, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            eligible.add(r["organisation_number"])
            if (r.get("website") or "").strip():
                had_site.add(r["organisation_number"])
    print(f"eligible universe        : {len(eligible):,}")
    print(f"  with registry website  : {len(had_site):,} ({100*len(had_site)/len(eligible):.2f}%)")
    no_site = eligible - had_site
    print(f"  WITHOUT                : {len(no_site):,} ({100*len(no_site)/len(eligible):.2f}%)")

    stats = collections.Counter()
    corp_email_domains: dict[str, str] = {}
    seen = 0
    for e in iter_bulk(BULK):
        org = e.get("organisasjonsnummer")
        if org not in eligible:
            continue
        seen += 1
        for field in ("epostadresse", "telefon", "mobil", "hjemmeside", "vedtektsfestetFormaal", "historiskeNavn", "kapital", "stiftelsesdato"):
            v = e.get(field)
            if v:
                stats[f"has_{field}"] += 1
        email = (e.get("epostadresse") or "").strip().lower()
        if email and "@" in email:
            dom = email.rsplit("@", 1)[-1].strip(" .>")
            if dom and "." in dom and not re.search(r"\s", dom):
                stats["email_any"] += 1
                if dom in FREEMAIL:
                    stats["email_freemail"] += 1
                else:
                    stats["email_corporate"] += 1
                    if org in no_site:
                        corp_email_domains[org] = dom
    print(f"\nmatched in bulk export   : {seen:,}")
    print("\n--- field availability across the eligible universe ---")
    for k, v in stats.most_common():
        print(f"  {k:<28} {v:>8,}  ({100*v/len(eligible):.2f}%)")

    print("\n--- THE CHANNEL ---")
    gained = len(corp_email_domains)
    print(f"companies with NO registry website but WITH a corporate e-mail domain: {gained:,}")
    print(f"  = +{100*gained/len(eligible):.2f} pp of the universe, a {100*gained/max(len(no_site),1):.2f}% recovery of the no-site group")
    print(f"potential website coverage: {100*(len(had_site)+gained)/len(eligible):.2f}%  (was {100*len(had_site)/len(eligible):.2f}%)")
    sample = list(corp_email_domains.items())[:10]
    print("\n  examples (org -> candidate domain, still requires live org-number proof):")
    for org, dom in sample:
        print(f"   {org} -> {dom}")
    out = "/Users/jd/Downloads/Case Competition/SignalPost/experiments/exp03_results.json"
    json.dump({"eligible": len(eligible), "with_registry_site": len(had_site),
               "email_domain_candidates": gained, "stats": dict(stats)},
              open(out, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
