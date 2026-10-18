"""Batch runner.

The contract is absolute: every input organisation number produces exactly one
terminal envelope on the output stream, in input order, whatever happens to the
sources. A crash inside one company is caught, recorded and emitted as a `failed`
envelope, because a missing row makes the whole run unscorable.

Progress is checkpointed, so an interrupted run resumes without re-requesting work
it has already completed and without emitting a company twice.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import AGENT_NAME, __version__
from .envelope import Availability, Claim, CompanyEnvelope, RunError, RunMeta, utcnow
from .http import Fetcher
from .pipeline import Pipeline, PipelineConfig
from .refresh import carry_forward, diff


def read_organisation_numbers(path: str | Path) -> list[str]:
    """Accept the batch file in any shape Builderr might supply it.

    Handles JSONL objects, a JSON array, and a plain list of numbers, and keeps
    input order with duplicates removed.
    """
    text = Path(path).read_text(encoding="utf-8")
    found: list[str] = []
    seen: set[str] = set()

    def push(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("organisation_number", "organisasjonsnummer", "orgnr",
                        "organizationNumber", "organisationNumber", "id"):
                if value.get(key):
                    push(value[key])
                    return
            return
        number = str(value).strip().strip('"').replace(" ", "")
        if number and number not in seen:
            seen.add(number)
            found.append(number)

    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            for row in json.loads(text):
                push(row)
            return found
        except json.JSONDecodeError:
            pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                push(json.loads(line))
                continue
            except json.JSONDecodeError:
                pass
        push(line)
    return found


def load_previous(path: str | Path | None) -> dict[str, CompanyEnvelope]:
    """Previous-run envelopes, keyed by organisation number, for the refresh diff."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, CompanyEnvelope] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            env = CompanyEnvelope.model_validate_json(line)
        except Exception:
            continue
        out[env.organisation_number] = env
    return out


@dataclass(slots=True)
class BatchConfig:
    organisations: str
    output: str
    run_id: str
    report: str | None = None
    previous: str | None = None
    workers: int = 6
    expected_count: int | None = None
    resume: bool = False
    timeout: float = 12.0
    per_host_delay: float = 0.4
    max_site_pages: int = 10
    max_candidates: int = 5
    enable_jobs: bool = True
    enable_website: bool = True
    nav_index_path: str | None = "cache/nav_jobs_index.json"
    # One bounded national snapshot, used only when the shipped index is
    # missing. 40 pages is ~1,000 active advertisements for a fixed ~60s,
    # independent of batch size.
    nav_snapshot_pages: int = 40
    time_budget_seconds: float | None = None


@dataclass(slots=True)
class BatchResult:
    envelopes_written: int = 0
    report: dict[str, Any] = field(default_factory=dict)


def _load_nav_index(path: str | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], dict[str, Any]]:
    if not path or not Path(path).exists():
        return {}, {}, {"present": False}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}, {}, {"present": False, "error": "unreadable"}
    meta = {k: v for k, v in data.items() if k not in {"index", "employer_homepages"}}
    meta["present"] = True
    return data.get("index") or {}, data.get("employer_homepages") or {}, meta


def run_batch(config: BatchConfig) -> BatchResult:
    organisations = read_organisation_numbers(config.organisations)
    if config.expected_count is not None and len(organisations) != config.expected_count:
        print(f"[warn] batch holds {len(organisations)} organisations, "
              f"--expected-count said {config.expected_count}")

    out_path = Path(config.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = out_path.with_suffix(out_path.suffix + ".progress")

    done: dict[str, str] = {}
    if config.resume and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                done[json.loads(line)["organisation_number"]] = line
            except Exception:
                continue
        print(f"[resume] {len(done)} envelopes already present")

    previous = load_previous(config.previous)
    nav_index, nav_homepages, nav_meta = _load_nav_index(config.nav_index_path)

    fetcher = Fetcher(timeout=config.timeout, per_host_delay=config.per_host_delay)
    pipeline = Pipeline(
        fetcher,
        PipelineConfig(run_id=config.run_id, max_site_pages=config.max_site_pages,
                       max_candidates=config.max_candidates,
                       enable_jobs=config.enable_jobs, enable_website=config.enable_website),
        nav_index=nav_index, nav_homepages=nav_homepages,
    )

    # One national snapshot of active advertisements for the whole batch. Cost is
    # fixed regardless of batch size; per-company search would be many hours for a
    # 1,000-company run, and a run that times out is not scored at all.
    nav_snapshot_meta: dict[str, Any] = {"attempted": False}
    if (
            config.enable_jobs
            and not nav_index
            and config.nav_snapshot_pages > 0
        ):
        print("[nav] building the run snapshot of active advertisements ...")
        try:
            nav_snapshot_meta = {"attempted": True,
                                 **pipeline.jobs.build_run_snapshot(max_pages=config.nav_snapshot_pages)}
        except Exception as exc:
            nav_snapshot_meta = {"attempted": True, "error": f"{type(exc).__name__}: {exc}"[:200]}
        print(f"[nav] {nav_snapshot_meta}")

    started = time.monotonic()
    started_at = utcnow()
    results: dict[str, CompanyEnvelope] = {}
    lock = threading.Lock()
    completed = Counter()
    latencies: list[int] = []
    budget_exhausted = threading.Event()

    def research(org: str) -> tuple[str, CompanyEnvelope]:
        if budget_exhausted.is_set():
            return org, _budget_envelope(org, config.run_id)
        try:
            env = pipeline.run(org)
        except BaseException as exc:  # noqa: BLE001 - a row must never be lost
            env = _failed_envelope(org, config.run_id, exc)
        prior = previous.get(org)
        if prior is not None:
            carry_forward(prior, env)
            env.changes = diff(prior, env)
            env.run.previous_snapshot_at = prior.run.completed_at or prior.run.started_at
        with lock:
            completed[str(env.status)] += 1
            latencies.append(env.operations.runtime_ms)
            if config.time_budget_seconds and (time.monotonic() - started) > config.time_budget_seconds:
                budget_exhausted.set()
            n = sum(completed.values())
            if n % 25 == 0:
                print(f"[{n}/{len(organisations)}] {time.monotonic()-started:.0f}s "
                      f"requests={fetcher.requests}")
            checkpoint.write_text(json.dumps({"completed": n, "run_id": config.run_id,
                                              "updated_at": utcnow()}), encoding="utf-8")
        return org, env

    pending = [o for o in organisations if o not in done]
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, config.workers)) as pool:
            for org, env in pool.map(research, pending):
                results[org] = env

    # Write in input order so output is deterministic and diffable.
    written = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for org in organisations:
            if org in results:
                fh.write(results[org].model_dump_json(exclude_none=False) + "\n")
            elif org in done:
                fh.write(done[org] + "\n")
            else:  # pragma: no cover - belt and braces
                fh.write(_failed_envelope(org, config.run_id,
                                          RuntimeError("no result produced")).model_dump_json() + "\n")
            written += 1

    elapsed = time.monotonic() - started
    report = _build_report(config, organisations, results, done, fetcher, elapsed,
                           started_at, latencies, nav_meta, written,
                           nav_snapshot_meta)
    if config.report:
        Path(config.report).parent.mkdir(parents=True, exist_ok=True)
        Path(config.report).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    if checkpoint.exists():
        checkpoint.unlink()
    return BatchResult(envelopes_written=written, report=report)


def _failed_envelope(org: str, run_id: str, exc: BaseException) -> CompanyEnvelope:
    env = CompanyEnvelope(
        organisation_number=org, status=Availability.FAILED,
        run=RunMeta(run_id=run_id, agent_version=f"{AGENT_NAME}/{__version__}",
                    started_at=utcnow(), completed_at=utcnow(), terminal_status="failed"),
    )
    env.errors.append(RunError(stage="pipeline", kind=type(exc).__name__,
                               detail=str(exc)[:300], retryable=True))
    env.claims.append(Claim(field="legal_identity", family="identity", value=None,
                            availability=Availability.FAILED,
                            note="research failed for this company; no value is asserted"))
    return env


def _budget_envelope(org: str, run_id: str) -> CompanyEnvelope:
    env = CompanyEnvelope(
        organisation_number=org, status=Availability.FAILED,
        run=RunMeta(run_id=run_id, agent_version=f"{AGENT_NAME}/{__version__}",
                    started_at=utcnow(), completed_at=utcnow(), terminal_status="failed"),
    )
    env.errors.append(RunError(stage="budget", kind="time_budget_exhausted",
                               detail="run time budget reached before this company was researched",
                               retryable=True))
    env.claims.append(Claim(field="legal_identity", family="identity", value=None,
                            availability=Availability.FAILED,
                            note="not researched: the run time budget was exhausted"))
    return env


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def _build_report(config: BatchConfig, organisations: list[str],
                  results: dict[str, CompanyEnvelope], done: dict[str, str],
                  fetcher: Fetcher, elapsed: float, started_at: str,
                  latencies: list[int], nav_meta: dict[str, Any],
                  written: int, nav_snapshot_meta: dict[str, Any] | None = None
                  ) -> dict[str, Any]:
    envs = list(results.values())
    field_states: dict[str, Counter] = {}
    tiers = Counter()
    changes = Counter()
    errors = Counter()
    conflicts = 0
    for env in envs:
        for claim in env.claims:
            field_states.setdefault(claim.field, Counter())[str(claim.availability)] += 1
            if claim.field == "official_website" and claim.availability == Availability.AVAILABLE:
                tiers[(claim.value or {}).get("verification", {}).get("tier", "unknown")] += 1
        for change in env.changes:
            changes[change.change_type] += 1
        for err in env.errors:
            errors[err.kind] += 1
        for attempt in env.summary.get("identity_attempts", []) or []:
            if attempt.get("conflicts"):
                conflicts += 1
    return {
        "run_id": config.run_id,
        "agent": f"{AGENT_NAME}/{__version__}",
        "started_at": started_at,
        "completed_at": utcnow(),
        "inputs": len(organisations),
        "envelopes_written": written,
        "one_envelope_per_input": written == len(organisations),
        "resumed_from_previous_output": len(done),
        "runtime_seconds": round(elapsed, 1),
        "requests": fetcher.requests,
        "bytes_downloaded": fetcher.bytes_downloaded,
        "third_party_cost_usd": 0.0,
        "cost_model": "official open-data APIs only; no paid connector enabled",
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": max(latencies) if latencies else 0,
        },
        "terminal_status": Counter(e.run.terminal_status for e in envs),
        "field_availability": {k: dict(v) for k, v in sorted(field_states.items())},
        "website_publication_tiers": dict(tiers),
        "candidates_rejected_for_conflicting_org_number": conflicts,
        "material_changes": dict(changes),
        "errors": dict(errors),
        "nav_jobs_index": nav_meta,
        "nav_run_snapshot": nav_snapshot_meta or {"attempted": False},
        "declared_caches": [
            {"name": "nav_jobs_index",
             "role": "candidate index only; every advertisement re-fetched and re-verified live",
             "present": nav_meta.get("present", False),
             "built_at": nav_meta.get("built_at")},
        ],
    }
