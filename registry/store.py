"""Version store — immutable capability version rows + lifecycle transitions.

A published name@version row's *content* is never rewritten: re-publishing the
same identity with different content is an error (git history is the amend
path). The one exception is a format migration — when the stored JSON is
semantically identical to the new one but serializes differently under the
current model (schema added default fields), the row is re-serialized in place.
Lifecycle stage, security status, and runtime evaluation are mutable
*columns* — they track the governance/operational state of a version without
touching its immutable manifest bytes (evaluation in particular is
runtime-derived and must never reach git).
"""
import json
import time
from typing import Any, Optional

from registry.db import Database
from schema.capability import (
    CapabilityEvaluationRef,
    CapabilityManifest,
    LifecycleStage,
    ModelProfileSpec,
    ToolSpec,
    WorkflowSpec,
    semver_key,
)


class VersionConflictError(Exception):
    """name@version already exists with different content (versions are immutable)."""


class InvalidTransitionError(Exception):
    """Lifecycle transition not allowed from the current stage."""


ALLOWED_TRANSITIONS: dict[str, frozenset] = {
    "draft": frozenset({"review"}),
    "review": frozenset({"draft", "approved"}),
    "approved": frozenset({"published"}),
    "published": frozenset({"deprecated"}),
    "deprecated": frozenset({"retired"}),
}


def extract_artifact(manifest: CapabilityManifest) -> dict[str, Any]:
    """The consumable payload of a manifest, per kind (what 'Use' returns)."""
    spec = manifest.spec
    if isinstance(spec, ToolSpec):
        return spec.tool.model_dump(mode="json")
    if isinstance(spec, ModelProfileSpec):
        return spec.model.model_dump(mode="json")
    if isinstance(spec, WorkflowSpec):
        if spec.workflow is not None:
            return spec.workflow.model_dump(mode="json")
        return {"workflow_ref": spec.workflow_ref}
    # prompt / skill / agent — the spec itself is the payload
    return {k: v for k, v in spec.model_dump(mode="json").items() if k != "kind"}


async def upsert_version(
    db: Database,
    manifest: CapabilityManifest,
    source_commit: Optional[str] = None,
) -> bool:
    """Insert a version row + FTS entry. Returns True when a new row was added.

    Re-syncing identical content is a no-op; different content under the same
    name@version raises VersionConflictError.
    """
    name, version = manifest.name, manifest.version
    rows = await db.conn.execute_fetchall(
        "SELECT manifest_json FROM capability_versions WHERE name=? AND version=?",
        (name, version),
    )
    new_manifest_json = json.dumps(manifest.model_dump(mode="json"), sort_keys=True)
    if rows:
        if rows[0]["manifest_json"] == new_manifest_json:
            return False
        # Schema evolution: a row written under an older model serializes
        # differently (new default fields) even when the content is identical.
        # Re-validate the stored row through the current model — if it
        # round-trips to the same dump, this is a format migration, not a
        # content change: rewrite the row in place and treat it as a no-op.
        try:
            stored = CapabilityManifest.model_validate_json(rows[0]["manifest_json"])
            stored_dump = json.dumps(stored.model_dump(mode="json"), sort_keys=True)
        except Exception:
            stored_dump = None
        if stored_dump == new_manifest_json:
            await db.conn.execute(
                "UPDATE capability_versions SET manifest_json=?, artifact_json=?"
                " WHERE name=? AND version=?",
                (
                    new_manifest_json,
                    json.dumps(extract_artifact(manifest), sort_keys=True),
                    name,
                    version,
                ),
            )
            await db.conn.commit()
            return False
        raise VersionConflictError(f"{name}@{version} already exists with different content")

    artifact = extract_artifact(manifest)
    created_at = manifest.created_at if manifest.created_at is not None else time.time()
    cur = await db.conn.execute(
        "INSERT INTO capability_versions"
        " (name, version, kind, manifest_json, artifact_json, stage,"
        "  security_status, source_commit, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            name,
            version,
            manifest.kind.value,
            new_manifest_json,
            json.dumps(artifact, sort_keys=True),
            manifest.stage.value,
            manifest.governance.security_status.value,
            source_commit,
            created_at,
        ),
    )
    await db.conn.execute(
        "INSERT INTO capability_fts (rowid, name, description, tags) VALUES (?,?,?,?)",
        (cur.lastrowid, name, manifest.description, " ".join(manifest.tags)),
    )
    await db.conn.commit()
    return True


def _manifest_from_row(r) -> CapabilityManifest:
    """The row's manifest with the stored runtime evaluation merged in.

    Evaluation lives in its own column (never in the manifest bytes or git);
    it is only attached here so every manifest response carries it. The key
    is always present in dumps — null when no evaluation is stored.
    """
    manifest = CapabilityManifest.model_validate_json(r["manifest_json"])
    if r["evaluation"] is not None:
        manifest.evaluation = CapabilityEvaluationRef.model_validate(
            json.loads(r["evaluation"])
        )
    return manifest


async def get_versions(db: Database, name: str) -> list[dict[str, Any]]:
    """All versions of a capability, newest first (semver order)."""
    rows = await db.conn.execute_fetchall(
        "SELECT * FROM capability_versions WHERE name=?", (name,)
    )
    versions = [
        {
            "name": r["name"],
            "version": r["version"],
            "kind": r["kind"],
            "stage": r["stage"],
            "security_status": r["security_status"],
            "source_commit": r["source_commit"],
            "created_at": r["created_at"],
            "manifest": _manifest_from_row(r),
        }
        for r in rows
    ]
    versions.sort(key=lambda v: semver_key(v["version"]), reverse=True)
    return versions


async def resolve_version(
    db: Database, name: str, version: str = "latest"
) -> dict[str, Any]:
    """Resolve a version selector to a concrete version row.

    'latest' = newest PUBLISHED version (semver order). Raises KeyError when
    the capability/version is unknown, LookupError when nothing is published.
    """
    if version != "latest":
        rows = await db.conn.execute_fetchall(
            "SELECT * FROM capability_versions WHERE name=? AND version=?",
            (name, version),
        )
        if not rows:
            raise KeyError(f"{name}@{version} not found")
        return _row_to_version(rows[0])

    any_rows = await db.conn.execute_fetchall(
        "SELECT 1 FROM capability_versions WHERE name=? LIMIT 1", (name,)
    )
    if not any_rows:
        raise KeyError(f"capability {name} not found")
    rows = await db.conn.execute_fetchall(
        "SELECT * FROM capability_versions WHERE name=? AND stage='published'", (name,)
    )
    if not rows:
        raise LookupError(f"{name} has no published version")
    best = max(rows, key=lambda r: semver_key(r["version"]))
    return _row_to_version(best)


def _row_to_version(r) -> dict[str, Any]:
    return {
        "name": r["name"],
        "version": r["version"],
        "kind": r["kind"],
        "stage": r["stage"],
        "security_status": r["security_status"],
        "source_commit": r["source_commit"],
        "created_at": r["created_at"],
        "manifest": _manifest_from_row(r),
    }


async def get_artifact(
    db: Database, name: str, version: str = "latest"
) -> dict[str, Any]:
    """The consumable payload for a resolved version (what 'Use' returns)."""
    resolved = await resolve_version(db, name, version)
    row = await db.conn.execute_fetchall(
        "SELECT artifact_json FROM capability_versions WHERE name=? AND version=?",
        (name, resolved["version"]),
    )
    return {
        "name": name,
        "version": resolved["version"],
        "kind": resolved["kind"],
        "stage": resolved["stage"],
        "artifact": json.loads(row[0]["artifact_json"]),
        "manifest": resolved["manifest"].model_dump(mode="json"),
    }


async def list_capabilities(db: Database) -> list[dict[str, Any]]:
    """One summary row per capability name (all stages)."""
    rows = await db.conn.execute_fetchall("SELECT * FROM capability_versions")
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)

    out = []
    for name, vers in sorted(by_name.items()):
        newest = max(vers, key=lambda r: semver_key(r["version"]))
        manifest = CapabilityManifest.model_validate_json(newest["manifest_json"])
        published = [r for r in vers if r["stage"] == "published"]
        latest_published = (
            max(published, key=lambda r: semver_key(r["version"]))["version"]
            if published else None
        )
        out.append({
            "name": name,
            "kind": newest["kind"],
            "description": manifest.description,
            "tags": manifest.tags,
            "spec": extract_artifact(manifest),
            "version_count": len(vers),
            "newest_version": newest["version"],
            "latest_published": latest_published,
            "updated_at": max(r["created_at"] for r in vers),
        })
    return out


def _quality_factor(evaluation_json: Optional[str]) -> float:
    """Ranking multiplier derived from a version's runtime evaluation score.

    Formula: 0.9 + 0.2 * score, mapping a score in [0, 1] to a factor in
    [0.9, 1.1]. A high success rate nudges a capability slightly above its
    equal-relevance peers; a low one slightly below — a gentle quality signal
    layered on top of FTS5 relevance, never strong enough to override it.

    Unmeasured is neutral: when the evaluation is NULL, malformed JSON, lacks
    a numeric `score`, or the score falls outside [0, 1], the factor is exactly
    1.0. Capabilities are never penalized for lacking evaluation data.
    """
    if evaluation_json is None:
        return 1.0
    try:
        data = json.loads(evaluation_json)
    except (ValueError, TypeError):
        return 1.0
    if not isinstance(data, dict):
        return 1.0
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return 1.0
    if not 0 <= score <= 1:
        return 1.0
    return 0.9 + 0.2 * score


async def search(
    db: Database, query: str, kind: Optional[str] = None, limit: int = 50
) -> list[dict[str, Any]]:
    """FTS5 keyword search over name/description/tags, one row per capability."""
    terms = [f'"{t}"' for t in query.split() if t]
    if not terms:
        return []
    match = " ".join(terms)

    sql = (
        "SELECT cv.*, bm25(capability_fts) AS rank FROM capability_fts"
        " JOIN capability_versions cv ON cv.rowid = capability_fts.rowid"
        " WHERE capability_fts MATCH ?"
    )
    params: list[Any] = [match]
    if kind:
        sql += " AND cv.kind = ?"
        params.append(kind)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit * 3)

    rows = await db.conn.execute_fetchall(sql, params)
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)

    out = []
    for name, vers in by_name.items():
        published = [r for r in vers if r["stage"] == "published"]
        pool = published or vers
        best = max(pool, key=lambda r: semver_key(r["version"]))
        manifest = CapabilityManifest.model_validate_json(best["manifest_json"])
        score = -best["rank"]  # raw bm25-derived relevance (exposed unchanged)
        out.append((score * _quality_factor(best["evaluation"]), {
            "name": name,
            "kind": best["kind"],
            "description": manifest.description,
            "tags": manifest.tags,
            "spec": extract_artifact(manifest),
            "version": best["version"],
            "stage": best["stage"],
            "score": score,
        }))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in out[:limit]]


async def transition_stage(
    db: Database, name: str, version: str, new_stage: LifecycleStage
) -> str:
    """Advance a version through the lifecycle state machine."""
    rows = await db.conn.execute_fetchall(
        "SELECT stage FROM capability_versions WHERE name=? AND version=?",
        (name, version),
    )
    if not rows:
        raise KeyError(f"{name}@{version} not found")
    current = rows[0]["stage"]
    if new_stage.value not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"cannot transition {current} -> {new_stage.value}"
        )
    await db.conn.execute(
        "UPDATE capability_versions SET stage=? WHERE name=? AND version=?",
        (new_stage.value, name, version),
    )
    await db.conn.commit()
    return new_stage.value


async def set_evaluation(
    db: Database, name: str, version: str, evaluation: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Store (or clear, with None) the runtime evaluation for a version.

    Runtime-derived metadata, updated straight in SQLite like stage/security
    status — never written to the manifest bytes or the git repo.
    """
    rows = await db.conn.execute_fetchall(
        "SELECT 1 FROM capability_versions WHERE name=? AND version=?",
        (name, version),
    )
    if not rows:
        raise KeyError(f"{name}@{version} not found")
    payload = json.dumps(evaluation, sort_keys=True) if evaluation is not None else None
    await db.conn.execute(
        "UPDATE capability_versions SET evaluation=? WHERE name=? AND version=?",
        (payload, name, version),
    )
    await db.conn.commit()
    return evaluation
