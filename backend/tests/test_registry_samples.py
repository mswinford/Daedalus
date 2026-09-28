"""Tests for the bundled sample manifests and the CLI publish/seed flow."""
import json
import sqlite3
import sys

import pytest

from registry.cli import SAMPLES_DIR, _publish, main
from schema.capability import CapabilityManifest

CORE_KINDS = {"tool", "prompt", "model_profile", "skill", "agent", "workflow"}


def _sample_manifests() -> list[CapabilityManifest]:
    return [
        CapabilityManifest.model_validate_json(f.read_text())
        for f in sorted(SAMPLES_DIR.glob("*.json"))
    ]


def test_samples_all_validate_and_cover_core_kinds():
    manifests = _sample_manifests()
    assert len(manifests) == len(list(SAMPLES_DIR.glob("*.json")))
    assert {m.kind.value for m in manifests} == CORE_KINDS


def test_sample_references_resolve_within_samples():
    """Every capability ref inside a sample must point at another sample."""
    manifests = _sample_manifests()
    known = {m.name for m in manifests}

    def refs(m: CapabilityManifest) -> list[str]:
        out = [r.name for r in m.dependencies]
        spec = m.spec
        if hasattr(spec, "tools"):
            out += [r.name for r in spec.tools]
        if hasattr(spec, "skills"):
            out += [r.name for r in spec.skills]
        if hasattr(spec, "model_profile"):
            out.append(spec.model_profile.name)
        return out

    for m in manifests:
        for ref in refs(m):
            assert ref in known, f"{m.name} references unknown capability {ref}"


@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("DAEDALUS_CAPABILITIES_REPO", str(tmp_path / "capabilities"))
    return tmp_path


def _count_rows(db_path) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM capability_versions").fetchone()[0]
    finally:
        conn.close()


def test_seed_publishes_all_samples(isolated_registry):
    rc = _publish(sorted(SAMPLES_DIR.glob("*.json")))
    assert rc == 0
    assert _count_rows(isolated_registry / "registry.db") == len(list(SAMPLES_DIR.glob("*.json")))


def test_seed_is_idempotent(isolated_registry):
    samples = sorted(SAMPLES_DIR.glob("*.json"))
    assert _publish(samples) == 0
    assert _publish(samples) == 0
    assert _count_rows(isolated_registry / "registry.db") == len(list(SAMPLES_DIR.glob("*.json")))


def test_publish_conflict_rejected(isolated_registry):
    assert _publish(sorted(SAMPLES_DIR.glob("*.json"))) == 0

    tampered = json.loads(
        (SAMPLES_DIR / "acme__courteous-assistant-prompt.json").read_text()
    )
    tampered["description"] = "tampered content"
    bad = isolated_registry / "bad.json"
    bad.write_text(json.dumps(tampered))

    assert _publish([bad]) == 1
    assert _count_rows(isolated_registry / "registry.db") == len(list(SAMPLES_DIR.glob("*.json")))


def test_publish_invalid_manifest_rejected(isolated_registry):
    bad = isolated_registry / "bad.json"
    bad.write_text('{"name": "no-slash", "version": "1.0.0"}')
    assert _publish([bad]) == 1
    assert _count_rows(isolated_registry / "registry.db") == 0


def test_cli_seed_command(isolated_registry, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daedalus-registry", "seed"])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 0
    assert _count_rows(isolated_registry / "registry.db") == len(list(SAMPLES_DIR.glob("*.json")))
