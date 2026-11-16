# The explorer

A company explorer with no build step, no framework and no server. Open it:

```bash
open viewer/index.html          # or just double-click the file
```

That is the whole instruction. The previous version of this page loaded its data
with `fetch("profiles.jsonl")`, which a browser refuses on a `file://` page — the
page's origin is opaque, so a sibling file is a cross-origin request. The failure
was silent, inside a `.catch(() => {})`, so anyone who opened the file directly
instead of starting a web server got a styled but empty shell. The run is now
published as `profiles.js`, a classic script, which loads either way.

`profiles.jsonl` is still here, one envelope per line, because that is what `jq`
wants. Both files are written by one command and `verify.py` fails if they
disagree:

```bash
./.venv/bin/python tools/build_viewer.py            # regenerate both
./.venv/bin/python tools/build_viewer.py --check    # assert they agree
```

**Open a run file** in the sidebar points the page at any other envelope file, so
an evaluator can read the output of their own run without rebuilding anything.

## The four views

**Companies** — every company in the run, each line opening on the one fact its
filed accounts or the entity register put first, with the register named in the
sentence. Search covers legal name, organisation number, domain and municipality.
Filter by website proved, website abstained, negative equity, a loss-making year,
adverse registry status, an open advertisement, or match confidence.

**A company** — the latest filed year and its movement, the registry identity, and
a tabbed panel: the source trail, the filed accounts, the registered roles, the
registered workplaces. Beneath the identity panel is *Checked, not found*: every
field that was looked for and not located, by name, with the reason.

**Coverage** — counted from the envelopes loaded in the page, not asserted. Field
by state, how each published website was proved, and what each summary opens on.

**How a match is proved** and **Registers and licences** — the identity ladder with
this run's count on each rung, the conflicting-organisation-number veto, the six
availability states, and the three different permissions at work in one envelope.

## What every claim carries

The source URL as a live link, the retrieval timestamp, the first 20 hex of the
content SHA-256, the licence, and the HTTP status. A reader who doubts any single
figure can open the page it was read from.

A domain the identity gate considered and refused is never named anywhere in this
interface. The scored run lost 24 claims to exactly that: the candidate log sat
in the summary block and a reader took a refused domain for a finding. Refusals
are reported here as counts and reasons.

Light and dark, system theme by default with a manual switch, and a single
column at phone width with no horizontal scroll.
