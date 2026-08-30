"""Thin CLI entry point for the capability registry (mirrors ai_forge/cli.py).

Commands:
    serve     Run the registry HTTP server (default when no command is given)
    publish   Publish one or more manifest JSON files into the capabilities repo
    seed      Publish the bundled sample manifests from registry/samples/

Publishing works offline (no server needed): it validates each manifest, writes
it into the capabilities git repo, commits, and syncs the SQLite index.
"""
import argparse
import json
import sys
from pathlib import Path


def _bootstrap_sys_path() -> None:
    """Make `schema` (repo root) importable when running from a source checkout."""
    try:
        import schema  # noqa: F401
    except ImportError:
        repo_root = str(Path(__file__).resolve().parent.parent)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)


SAMPLES_DIR = Path(__file__).resolve().parent / "samples"


def _serve(host: str | None, port: int | None) -> None:
    import uvicorn

    from registry.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "registry.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=True,
    )


async def _publish_async(paths: list[Path]) -> int:
    from registry.config import get_settings
    from registry.db import Database
    from registry.indexer import (
        commit_all,
        ensure_repo,
        sync_from_repo,
        write_manifest_to_repo,
    )
    from schema.capability import CapabilityManifest

    settings = get_settings()

    # 1) Validate everything up front — fail before touching the repo.
    manifests: list[CapabilityManifest] = []
    for path in paths:
        try:
            raw = path.read_text()
            manifests.append(CapabilityManifest.model_validate_json(raw))
        except Exception as e:
            print(f"error: {path}: invalid manifest: {e}", file=sys.stderr)
            return 1

    db = await Database.connect(settings.registry_db)
    try:
        await ensure_repo(settings.capabilities_repo)

        # 2) Classify each manifest against the current index.
        to_write: list[CapabilityManifest] = []
        conflicts = 0
        for m in manifests:
            new_json = json.dumps(m.model_dump(mode="json"), sort_keys=True)
            rows = await db.conn.execute_fetchall(
                "SELECT manifest_json FROM capability_versions WHERE name=? AND version=?",
                (m.name, m.version),
            )
            if rows:
                if rows[0]["manifest_json"] == new_json:
                    print(f"unchanged  {m.name}@{m.version} (already in registry)")
                else:
                    conflicts += 1
                    print(
                        f"conflict   {m.name}@{m.version} exists with different content; "
                        f"publish a new version",
                        file=sys.stderr,
                    )
            else:
                to_write.append(m)

        # 3) Write the new ones, commit once, sync the index.
        if to_write:
            for m in to_write:
                await write_manifest_to_repo(settings.capabilities_repo, m)
            await commit_all(
                settings.capabilities_repo,
                f"publish {', '.join(f'{m.name}@{m.version}' for m in to_write)}",
            )
            report = await sync_from_repo(settings.capabilities_repo, db)
            if report["conflicts"]:
                conflicts += len(report["conflicts"])
            for m in to_write:
                print(f"published  {m.name}@{m.version} ({m.kind.value})")
    finally:
        await db.close()

    return 1 if conflicts else 0


def _publish(paths: list[Path]) -> int:
    import asyncio

    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            print(f"error: file not found: {p}", file=sys.stderr)
        return 1
    return asyncio.run(_publish_async(paths))


def main() -> None:
    _bootstrap_sys_path()

    parser = argparse.ArgumentParser(
        prog="ai-forge-registry",
        description="AI Forge capability registry",
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="run the registry HTTP server (default)")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)

    pub_p = sub.add_parser(
        "publish", help="publish one or more manifest JSON files"
    )
    pub_p.add_argument("manifests", nargs="+", help="path(s) to manifest JSON files")

    sub.add_parser("seed", help="publish the bundled sample manifests")

    args = parser.parse_args()

    if args.command in (None, "serve"):
        _serve(getattr(args, "host", None), getattr(args, "port", None))
    elif args.command == "publish":
        sys.exit(_publish([Path(p) for p in args.manifests]))
    elif args.command == "seed":
        samples = sorted(SAMPLES_DIR.glob("*.json"))
        if not samples:
            print(f"error: no sample manifests found in {SAMPLES_DIR}", file=sys.stderr)
            sys.exit(1)
        sys.exit(_publish(samples))


if __name__ == "__main__":
    main()
