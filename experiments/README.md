# Experiments

The measurements that shaped the design. Each is runnable and writes its raw
results next to it, so the numbers quoted in `README.md`, `EVAL.md` and
`LIMITATIONS.md` can be re-derived rather than taken on trust.

| script | question | answer |
|---|---|---|
| `exp01_domain_discovery.py` | can a domain be guessed from the legal name? | 4.7% verified; **10.7% of matches belonged to another company** |
| `exp02_deep_verify.py` | does crawling contact/privacy pages convert the unproven? | **+0.0pp** — those domains are parked or unrelated |
| `exp03_email_domain_channel.py` | what does the registry's own e-mail field unlock? | website coverage 10.91% → **20.47%** potential |
| `exp04_validate_email_channel.py` | does demanding the org number verify that channel? | only 2.0% — *worse* than guessing, because 56.7% of sites never print it |
| `exp05_identity_scorer.py` | what does a multi-signal gate cost in precision? | produced the calibration curve and the two failure cases the shipped gate now rejects |

Experiments 02 and 04 are negative results. They are kept because they are the
reason the shipped gate is tiered rather than strict, and the reason phase 2 of
website resolution runs on one candidate instead of all of them.

Run them with the project venv, e.g.

```bash
./.venv/bin/python experiments/exp01_domain_discovery.py 150
```

`exp03` and `exp04` need the full Enhetsregisteret bulk export, which is not
committed:

```bash
curl -L 'https://data.brreg.no/enhetsregisteret/api/enheter/lastned' -o enheter.json.gz
```
