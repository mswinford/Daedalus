"""Per-capability runtime metrics aggregated from terminal run rows.

One pass over every finished run's capability_usage snapshot ({name:
version-or-null}). The result is keyed by (name, version) and shaped like the
registry evaluation endpoint body (score + stats), so a follow-up can push it
straight to PUT /capabilities/{name}/versions/{version}/evaluation.
"""
import asyncio
import json
import logging
import math
import time

from app.capability_client import CapabilityClient, CapabilityNotFoundError
from app.runs.store import _load_terminal_runs, flush_store

logger = logging.getLogger(__name__)


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list.

    index = max(0, ceil(q * n) - 1), so a single value is its own percentile
    for any q in (0, 1].
    """
    idx = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[idx]


def compute_capability_aggregates() -> dict[tuple[str, str | None], dict]:
    """Aggregate score/duration/cost metrics per (capability name, version).

    Defensive: runs whose capability_usage is NULL or malformed JSON are
    skipped entirely; a run with completed_at NULL still counts toward
    totals/score/avg cost but not toward the duration percentiles (which are
    None when no contributing run has a duration).
    """
    acc: dict[tuple[str, str | None], dict] = {}
    for row in _load_terminal_runs():
        raw = row["capability_usage"]
        if not raw:
            continue
        try:
            usage = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(usage, dict):
            continue
        for name, version in usage.items():
            entry = acc.setdefault(
                (name, version), {"total": 0, "failed": 0, "durations": [], "costs": []}
            )
            entry["total"] += 1
            if row["status"] == "failed":
                entry["failed"] += 1
            if row["completed_at"] is not None:
                entry["durations"].append((row["completed_at"] - row["started_at"]) * 1000.0)
            entry["costs"].append(row["estimated_cost_usd"])

    result: dict[tuple[str, str | None], dict] = {}
    for key, e in acc.items():
        total, failed = e["total"], e["failed"]
        durations = sorted(e["durations"])
        result[key] = {
            "score": round((total - failed) / total, 4),
            "stats": {
                "runs_total": total,
                "runs_failed": failed,
                "duration_ms_p50": _percentile(durations, 0.50) if durations else None,
                "duration_ms_p95": _percentile(durations, 0.95) if durations else None,
                "avg_cost_usd": round(sum(e["costs"]) / len(e["costs"]), 6),
            },
        }
    return result


def _report_run_metrics_sync(record) -> None:
    """Aggregate once and push this run's versioned capabilities to the registry.

    Runs in a worker thread (see report_run_metrics). Flushes the store first:
    the caller's terminal summary is only queued at this point, so the
    aggregation pass would otherwise race it.
    """
    flush_store()
    aggregates = compute_capability_aggregates()
    client = CapabilityClient()
    for name, version in record.capability_usage.items():
        if version is None:
            continue  # name-only provenance has no registry endpoint
        agg = aggregates.get((name, version))
        if agg is None:
            continue
        payload = {
            "score": agg["score"],
            "last_scored_at": time.time(),
            "stats": agg["stats"],
        }
        try:
            client.write_evaluation(name, version, payload)
        except CapabilityNotFoundError:
            logger.info(
                "evaluation push skipped for %s@%s (capability no longer in registry)",
                name, version,
            )
        except Exception as exc:  # noqa: BLE001 - a failed push must never affect the run
            logger.warning("evaluation push failed for %s@%s: %s", name, version, exc)


async def report_run_metrics(record) -> None:
    """Terminal-run hook: push this run's per-capability metrics to the registry.

    No-op unless the run is terminal (completed/failed) and carries a
    capability_usage snapshot. Total-safe by contract: any failure — including
    one inside the aggregation pass — is logged and swallowed, so a metrics
    problem can never fail or delay a run's terminal transition.
    """
    if record.status not in ("completed", "failed") or not record.capability_usage:
        return
    try:
        await asyncio.to_thread(_report_run_metrics_sync, record)
    except Exception as exc:  # noqa: BLE001 - metrics must never break the run
        logger.warning("run metrics reporting failed for %s: %s", record.run_id, exc)
