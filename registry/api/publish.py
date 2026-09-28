"""Publish endpoint — validates a manifest, commits it to the capabilities
git repo, and indexes it into the DB."""
import json

from fastapi import APIRouter, HTTPException, Request

from registry.config import get_settings
from registry.indexer import commit_all, ensure_repo, sync_from_repo, write_manifest_to_repo
from registry.publish_checks import check_publish
from registry.store import VersionConflictError
from schema.capability import CapabilityManifest

router = APIRouter()


@router.post("/capabilities", status_code=201)
async def publish(request: Request, manifest: CapabilityManifest):
    """Note: the git commit happens before the DB sync. If the sync fails the
    response 500s but the git commit stands; the divergence self-heals on the
    next startup or publish because sync is a full rescan of the repo."""
    db = request.app.state.db
    settings = get_settings()

    existing = await db.conn.execute_fetchall(
        "SELECT manifest_json FROM capability_versions WHERE name=? AND version=?",
        (manifest.name, manifest.version),
    )
    new_manifest_json = json.dumps(manifest.model_dump(mode="json"), sort_keys=True)
    if existing:
        status = "already published" if existing[0]["manifest_json"] == new_manifest_json \
            else "version exists with different content; publish a new version"
        raise HTTPException(status_code=409, detail=status)

    errors = await check_publish(db, manifest)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    await ensure_repo(settings.capabilities_repo, settings.capabilities_remote or None)
    try:
        await write_manifest_to_repo(settings.capabilities_repo, manifest)
        source_commit = await commit_all(
            settings.capabilities_repo,
            f"publish {manifest.name}@{manifest.version}",
            remote=settings.capabilities_remote or None,
        )
        report = await sync_from_repo(settings.capabilities_repo, db)
    except VersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "name": manifest.name,
        "version": manifest.version,
        "kind": manifest.kind.value,
        "stage": manifest.stage.value,
        "source_commit": source_commit,
        "sync_report": report,
    }
