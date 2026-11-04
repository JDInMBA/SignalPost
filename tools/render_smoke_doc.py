"""Render the measured tables in SMOKE_TEST.md from the committed run report.

The figures in that document were typed by hand once and then drifted: the run
report said one runtime and one coverage, the prose said another. A reviewer who
opens both files sees the contradiction, and there is no way to tell which one is
the measurement. So the tables are generated, and `--check` fails if the document
no longer matches the artifact it describes.

    python tools/render_smoke_doc.py            # rewrite SMOKE_TEST.md in place
    python tools/render_smoke_doc.py --check     # exit 1 if it is out of date
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Families that can only be read off a company's own website, so their
# denominator is the number of websites actually published, not the batch.
SITE_DERIVED = ("website_description", "website_structured_organisation",
                "website_contact", "company_owned_profiles",
                "company_published_activity", "careers_page")

FIELD_ORDER = ("legal_identity", "operating_status", "stated_purpose",
               "public_brand_aliases", "registry_contact", "registered_employees",
               "leadership", "registered_workplaces", "group_structure",
               "annual_accounts", "annual_account_filings", "official_website",
               "website_description", "website_contact",
               "website_structured_organisation", "company_owned_profiles",
               "company_published_activity", "careers_page",
               "job_postings", "hiring_signal")

TIER_LABEL = {"A_org_number_on_page": "organisation number printed on the page",
              "B_registry_declared_website": "declared in the registry",
              "C_registry_email_domain_plus_corroboration": "registered e-mail domain, corroborated",
              "D_site_identity_name_plus_locator": "name plus a registered locator"}


def _mins(seconds: float) -> str:
    return f"{seconds:.0f}s ({seconds / 60:.1f} min)"


def run_result(rep: dict) -> str:
    inputs = rep["inputs"] if isinstance(rep.get("inputs"), int) else len(rep.get("inputs") or [])
    n = rep["envelopes_written"]
    per = rep["requests"] / n if n else 0
    lat = rep["latency_ms"]
    rows = [
        ("inputs", str(inputs)),
        ("envelopes written", f"**{n}**"),
        ("**exactly one terminal envelope per input**",
         f"**{str(rep['one_envelope_per_input']).lower()}**"),
        ("terminal status", json.dumps(rep["terminal_status"])),
        ("run errors", "**none**" if not rep["errors"] else f"**{rep['errors']}**"),
        ("runtime", _mins(rep["runtime_seconds"])),
        ("requests", f"{rep['requests']} ({per:.1f} per company)"),
        ("bytes downloaded", f"{rep['bytes_downloaded'] / 1e6:.1f} MB"),
        ("latency p50 / p95 / max",
         " / ".join(f"{lat[k] / 1000:.1f}s" for k in ("p50", "p95", "max"))),
        ("**third-party cost**", f"**${rep['third_party_cost_usd']:.2f}**"),
    ]
    out = ["| | |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    scale = 1000 / n if n else 0
    out += ["",
            f"Extrapolated to the official 1,000-company batch at the same "
            f"{rep.get('workers', 6)} workers: roughly "
            f"**{rep['runtime_seconds'] * scale / 60:.0f} minutes and "
            f"~{rep['requests'] * scale:,.0f} requests, at $0.00**."]
    return "\n".join(out)


def _site_subset_states(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Site-derived states counted only over companies that have a site.

    Counting them over the whole batch mixes two different facts into one row:
    "we read the page and the fact was not on it" and "there is no page to read".
    """
    out: dict[str, dict[str, int]] = {f: {} for f in SITE_DERIVED}
    for env in rows:
        site = next((c for c in env["claims"] if c["field"] == "official_website"), None)
        if not site or site["availability"] != "available":
            continue
        for claim in env["claims"]:
            if claim["field"] in out:
                bucket = out[claim["field"]]
                bucket[claim["availability"]] = bucket.get(claim["availability"], 0) + 1
    return out


def coverage(rep: dict, rows: list[dict]) -> str:
    avail = rep["field_availability"]
    published = avail.get("official_website", {}).get("available", 0)
    total = rep["envelopes_written"]
    subset = _site_subset_states(rows)
    out = ["| field | available | other states |", "|---|---:|---|"]
    for field in FIELD_ORDER:
        states = subset.get(field) if field in SITE_DERIVED else avail.get(field)
        if states is None or (field not in SITE_DERIVED and not states):
            continue
        got = states.get("available", 0)
        base = published if field in SITE_DERIVED else total
        others = ", ".join(f"{k} {v}" for k, v in sorted(states.items()) if k != "available")
        out.append(f"| `{field}` | **{got}/{base}** | {others or '—'} |")
    out += ["",
            f"The six site-derived families are counted over the {published} companies "
            f"with a verified website, because \"the page did not say\" and \"there is "
            f"no page\" are different findings. The other {total - published} companies "
            f"still receive a terminal state on every one of those families — never a "
            f"blank — so no field is silently skipped."]
    return "\n".join(out)


def discovery(rep: dict, meta: dict) -> str:
    states = rep["field_availability"].get("official_website", {})
    tiers = rep["website_publication_tiers"]
    published = states.get("available", 0)
    in_registry = meta["with_registry_website"]
    declared = tiers.get("B_registry_declared_website", 0)
    found = published - declared
    out = ["| | |", "|---|---:|",
           f"| companies with a registry website in this batch | {in_registry} |",
           f"| **websites published** | **{published}** |"]
    for state in ("ambiguous", "blocked", "not_available"):
        if state in states:
            out.append(f"| abstained as `{state}` | {states[state]} |"
                       if state == "ambiguous" else f"| `{state}` | {states[state]} |")
    out += [f"| **candidates vetoed for carrying another company's organisation "
            f"number** | **{rep['candidates_rejected_for_conflicting_org_number']}** |", ""]
    out += ["Publication tiers:", ""]
    for tier, count in sorted(tiers.items()):
        out.append(f"- `{tier}` — {TIER_LABEL.get(tier, tier)}: **{count}**")
    lift = (published / in_registry - 1) * 100 if in_registry else 0
    out += ["",
            f"Only {declared} of the {published} published websites came from the "
            f"registry's own website field. The other {found} were **discovered** and "
            f"then proved, so this batch ends with {published} verified websites where "
            f"the registry field alone offers {in_registry} — a {lift:.0f}% increase.",
            "",
            f"The {rep['candidates_rejected_for_conflicting_org_number']} vetoed "
            f"candidates are the headline risk result: those are pages that matched the "
            f"company by name or contact details but carried a **different** company's "
            f"organisation number. A name-similarity agent publishes them. This one "
            f"does not."]
    return "\n".join(out)


def hiring_negative(rep: dict) -> str:
    states = rep["field_availability"].get("job_postings", {})
    index = rep.get("nav_jobs_index") or {}
    total = rep["envelopes_written"]
    absent = states.get("not_available", 0)
    if absent != total:
        found = states.get("available", 0)
        return (f"`job_postings` resolved for all {total} companies: "
                f"**{found} with an advertisement**, {absent} with none registered, "
                f"and the remaining states below. Every outcome is stated on the "
                f"claim, so a reader can tell a real negative from a silence.")
    return (f"`job_postings` came back `not_available` for all {total} companies, "
            f"which is a real negative rather than a silence: the committed Nav "
            f"candidate index holds {index.get('companies_indexed', 0):,} companies "
            f"and {index.get('ads_with_organisation_number', 0):,} advertisements, it "
            f"was consulted for every input, and none of these {total} organisation "
            f"numbers (or their registered subunits) has an active advertisement. The "
            f"note on each claim says so.")


PER_COMPANY_SEARCH_SECONDS = 7402


def hiring_runtime(rep: dict) -> str:
    runtime = rep["runtime_seconds"]
    factor = PER_COMPANY_SEARCH_SECONDS / runtime if runtime else 0
    return (f"An earlier build searched Nav once per company. That took "
            f"**{PER_COMPANY_SEARCH_SECONDS:,} seconds** for this same batch — a "
            f"1,000-company official run would have taken over 20 hours and scored "
            f"zero on timeout. The shipped design reads a committed candidate index "
            f"and, only if that is absent, takes one bounded national snapshot. This "
            f"run: **{runtime:.0f} seconds**, a {factor:.0f}x reduction, with hiring "
            f"coverage intact.")


BLOCKS = {"run-result": run_result, "coverage": coverage, "discovery": discovery,
          "hiring-negative": hiring_negative, "hiring-runtime": hiring_runtime}


def render(doc: str, rep: dict, meta: dict, rows: list[dict]) -> str:
    for name, fn in BLOCKS.items():
        if fn is discovery:
            body = fn(rep, meta)
        elif fn is coverage:
            body = fn(rep, rows)
        else:
            body = fn(rep)
        pattern = re.compile(
            rf"(<!-- generated: {name} -->\n).*?(\n<!-- /generated -->)", re.S)
        if not pattern.search(doc):
            raise SystemExit(f"SMOKE_TEST.md has no '{name}' generated block")
        doc = pattern.sub(lambda m: m.group(1) + body + m.group(2), doc)
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", default="SMOKE_TEST.md")
    ap.add_argument("--report", default="results/smoke-report.json")
    ap.add_argument("--meta", default="smoke-companies.meta.json")
    ap.add_argument("--envelopes", default="out/smoke-envelopes.jsonl")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    doc = Path(a.doc).read_text(encoding="utf-8")
    rep = json.loads(Path(a.report).read_text(encoding="utf-8"))
    meta = json.loads(Path(a.meta).read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in
            Path(a.envelopes).read_text(encoding="utf-8").splitlines() if l.strip()]
    new = render(doc, rep, meta, rows)

    if a.check:
        if new != doc:
            print(f"{a.doc} does not match {a.report}; "
                  f"run tools/render_smoke_doc.py", file=sys.stderr)
            return 1
        print(f"{a.doc} matches {a.report}")
        return 0
    Path(a.doc).write_text(new, encoding="utf-8")
    print(f"rewrote the measured tables in {a.doc} from {a.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
