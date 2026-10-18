# Profile viewer

A dependency-free page for reading the agent's output: find a company, compare
companies, and verify any single fact back to its source.

```bash
cd viewer && python3 -m http.server 8777     # then open http://localhost:8777
```

It loads `profiles.jsonl` on open (the committed 100-company smoke run) and the
**Open JSONL** button loads any other envelope file, so an evaluator can point it
at the output of an official run without rebuilding anything.

## What it shows

Every claim is rendered with the three things the evaluation asks a reader to be
able to check:

- its **availability state** — `available`, `not_available`, `blocked`,
  `ambiguous`, `not_applicable` or `failed`, colour-coded and never collapsed;
- its **value**, with accounts, roles and job advertisements as tables;
- its **evidence** — the source URL as a live link, the retrieval timestamp, and
  the first 16 hex of the content SHA-256.

The summary leads each profile, and the fields that were checked and *not* found
are listed under it by name, so a reader sees the gaps as plainly as the hits.

Filters: verified website, currently hiring, website abstained, adverse registry
status. Search covers legal name, organisation number and domain.

Works on desktop and at phone width (single column, 16px gutter, no horizontal
scroll), follows the system light/dark theme, and has a manual theme toggle.
No framework, no network calls, no build step.
