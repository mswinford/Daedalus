"""Publish-time governance checks (R2 Govern & Compose).

Runs on the explicit publish paths (API endpoint + CLI) BEFORE anything is
committed to the capabilities repo. It deliberately does NOT run inside
sync_from_repo — that is a repair path (startup rescan, git-driven), and
governance failures there would be noise, not gatekeeping.

Four checks:

1. Dependency resolution — every capability ref in the manifest must resolve
   against the store with the same semantics import-time uses: an explicit
   version must exist (any stage); 'latest' must have a published version.
   Spec-level refs must also point at the expected kind, so wrong-kind refs
   fail here with a precise message instead of at import time. And because
   spec-level refs are inlined at import time, every secret they require must
   be declared in this manifest's secrets_required (composite coverage).

2. Kind stability — a capability name cannot change kind across versions;
   per the settled design that is a new capability under a new name.

3. Secret hygiene — a model's api_key_ref must be a secret *name* (never an
   embedded key value) and, when set, must be declared in secrets_required.
   Stops a literal API key from leaking into a published artifact.

4. Breaking-change detection — per-kind rules compare the new manifest
   against the highest existing lower version. Detected breaking changes
   require a major semver bump; otherwise the publish is rejected.
"""
import re
from typing import Any, Optional

from registry.db import Database
from schema.capability import (
    AgentSpec,
    CapabilityKind,
    CapabilityManifest,
    CapabilityRef,
    LifecycleStage,
    ModelProfileSpec,
    PromptSpec,
    SkillSpec,
    ToolSpec,
    WorkflowSpec,
    semver_key,
)


# ─── Ref collection ──────────────────────────────────────────────────────────

def collect_refs(manifest: CapabilityManifest) -> list[tuple[str, CapabilityRef]]:
    """All capability refs in a manifest as (slot, ref) pairs.

    Slot labels are human-readable paths into the manifest: 'model_profile',
    'prompt_ref', 'tools[0]', 'skills[1]', 'dependencies[2]'. Top-level
    dependencies carry no expected kind; spec-level slots do.
    """
    refs: list[tuple[str, CapabilityRef]] = []
    spec = manifest.spec
    if isinstance(spec, AgentSpec):
        refs.append(("model_profile", spec.model_profile))
        if spec.prompt_ref is not None:
            refs.append(("prompt_ref", spec.prompt_ref))
        for i, t in enumerate(spec.tools):
            refs.append((f"tools[{i}]", t))
        for i, s in enumerate(spec.skills):
            refs.append((f"skills[{i}]", s))
    elif isinstance(spec, SkillSpec):
        if spec.prompt_ref is not None:
            refs.append(("prompt_ref", spec.prompt_ref))
        for i, t in enumerate(spec.tools):
            refs.append((f"tools[{i}]", t))
    for i, d in enumerate(manifest.dependencies):
        refs.append((f"dependencies[{i}]", d))
    return refs


def _expected_kind(slot: str) -> Optional[str]:
    if slot.startswith("tools["):
        return "tool"
    if slot.startswith("skills["):
        return "skill"
    if slot == "model_profile":
        return "model_profile"
    if slot == "prompt_ref":
        return "prompt"
    return None


def _ref_str(ref: CapabilityRef) -> str:
    return f"{ref.name}@{ref.version}"


# ─── Dependency resolution ───────────────────────────────────────────────────

async def _resolve_target(
    db: Database, name: str, version: str, batch: list[CapabilityManifest]
) -> tuple[Optional[str], str]:
    """Resolve a ref to (kind, problem). kind is None when unresolvable.

    Resolution mirrors import-time semantics (store.resolve_version): an
    explicit version must exist at any stage; 'latest' requires a published
    version. Manifests in the current publish batch count as available —
    needed because the CLI can publish several capabilities in one call and
    they may reference each other (e.g. `seed`).
    """
    if version != "latest":
        rows = await db.conn.execute_fetchall(
            "SELECT kind FROM capability_versions WHERE name=? AND version=?",
            (name, version),
        )
        if rows:
            return rows[0]["kind"], ""
        for m in batch:
            if m.name == name and m.version == version:
                return m.kind.value, ""
        return None, f"{_ref_str(CapabilityRef(name=name, version=version))} not found"

    rows = await db.conn.execute_fetchall(
        "SELECT kind FROM capability_versions WHERE name=? AND stage=?",
        (name, LifecycleStage.PUBLISHED.value),
    )
    if rows:
        return rows[0]["kind"], ""
    for m in batch:
        if m.name == name and m.stage == LifecycleStage.PUBLISHED:
            return m.kind.value, ""
    any_rows = await db.conn.execute_fetchall(
        "SELECT 1 FROM capability_versions WHERE name=? LIMIT 1", (name,)
    )
    if any_rows:
        return None, f"{name} has no published version"
    return None, f"capability {name} not found"


async def _member_secrets_required(
    db: Database, name: str, version: str, batch: list[CapabilityManifest]
) -> Optional[list[str]]:
    """secrets_required declared by the capability a ref points at (None = unresolvable)."""
    if version != "latest":
        rows = await db.conn.execute_fetchall(
            "SELECT manifest_json FROM capability_versions WHERE name=? AND version=?",
            (name, version),
        )
        if rows:
            return CapabilityManifest.model_validate_json(rows[0]["manifest_json"]).secrets_required
        for m in batch:
            if m.name == name and m.version == version:
                return m.secrets_required
        return None

    rows = await db.conn.execute_fetchall(
        "SELECT manifest_json FROM capability_versions WHERE name=? AND stage=?",
        (name, LifecycleStage.PUBLISHED.value),
    )
    best: Optional[CapabilityManifest] = None
    for r in rows:
        m = CapabilityManifest.model_validate_json(r["manifest_json"])
        if best is None or semver_key(m.version) > semver_key(best.version):
            best = m
    if best is not None:
        return best.secrets_required
    for m in batch:
        if m.name == name and m.stage == LifecycleStage.PUBLISHED:
            return m.secrets_required
    return None


async def _check_dependencies(
    db: Database, manifest: CapabilityManifest, batch: list[CapabilityManifest]
) -> list[str]:
    errors = []
    for slot, ref in collect_refs(manifest):
        kind, problem = await _resolve_target(db, ref.name, ref.version, batch)
        if problem:
            errors.append(f"{slot}: {problem}")
            continue
        want = _expected_kind(slot)
        if want is not None and kind != want:
            errors.append(
                f"{slot}: {_ref_str(ref)} is kind '{kind}', expected '{want}'"
            )
        # Composite secret coverage: spec-level refs are inlined at import time, so
        # every secret the referenced capability needs must also be declared here —
        # otherwise a consumer importing this manifest never gets flagged for it.
        # Top-level dependencies are metadata only (not inlined) and are exempt.
        if not slot.startswith("dependencies["):
            member_secrets = await _member_secrets_required(
                db, ref.name, ref.version, batch
            )
            if member_secrets is not None:
                undeclared = [s for s in member_secrets if s not in manifest.secrets_required]
                if undeclared:
                    errors.append(
                        f"{slot}: {_ref_str(ref)} requires secret(s) "
                        f"{', '.join(sorted(undeclared))} which are not declared in "
                        "this manifest's secrets_required"
                    )
    return errors


# ─── Breaking-change detection (per-kind rules) ──────────────────────────────

def _json_schema_breaking(
    old: Optional[dict], new: Optional[dict], label: str
) -> list[str]:
    """Backward-compat check for a declared JSON Schema (interface in/out).

    Breaking: top-level type change, removed property, property retype,
    optional -> required. Adding properties stays non-breaking.
    """
    if old is None or new is None:
        return []
    if not isinstance(old, dict) or not isinstance(new, dict):
        return [f"{label} schema changed shape"] if old != new else []
    out = []
    if old.get("type") != new.get("type"):
        out.append(
            f"{label} type changed {old.get('type')!r} -> {new.get('type')!r}"
        )
        return out
    props_old = old.get("properties") or {}
    props_new = new.get("properties") or {}
    req_old = set(old.get("required") or [])
    req_new = set(new.get("required") or [])
    for pname, po in props_old.items():
        pn = props_new.get(pname)
        if pn is None:
            out.append(f"{label} removed '{pname}'")
            continue
        if (
            isinstance(po, dict)
            and isinstance(pn, dict)
            and po.get("type") != pn.get("type")
        ):
            out.append(
                f"{label} '{pname}' type changed "
                f"{po.get('type')!r} -> {pn.get('type')!r}"
            )
        if pname not in req_old and pname in req_new:
            out.append(f"{label} '{pname}' is now required")
    return out


def _tool_breaking(old: CapabilityManifest, new: CapabilityManifest) -> list[str]:
    """The consumable artifact is the ToolDefinition; its parameters are the
    contract. Output shape lives only in the declared interface."""
    ot = old.spec.tool  # type: ignore[union-attr]
    nt = new.spec.tool  # type: ignore[union-attr]
    out: list[str] = []
    if ot.id != nt.id:
        out.append(f"tool id changed '{ot.id}' -> '{nt.id}'")
    for pname, p in ot.parameters.items():
        np_ = nt.parameters.get(pname)
        if np_ is None:
            out.append(f"removed parameter '{pname}'")
            continue
        if p.type != np_.type:
            out.append(
                f"parameter '{pname}' type changed {p.type} -> {np_.type}"
            )
        if not p.required and np_.required:
            out.append(f"parameter '{pname}' is now required")
        old_e, new_e = p.enum, np_.enum
        if new_e is not None:
            if old_e is None:
                out.append(f"parameter '{pname}' now restricted to enum {new_e}")
            else:
                dropped = sorted(set(old_e) - set(new_e))
                if dropped:
                    out.append(
                        f"parameter '{pname}' enum values removed: {dropped}"
                    )
    if old.interface is not None and new.interface is not None:
        out += _json_schema_breaking(
            old.interface.output_schema, new.interface.output_schema, "output"
        )
    return out


def _workflow_breaking(old: CapabilityManifest, new: CapabilityManifest) -> list[str]:
    """Declared state in/out compatibility; internal graph edits are minor."""
    os_ = old.spec  # type: ignore[assignment]
    ns_ = new.spec  # type: ignore[assignment]
    assert isinstance(os_, WorkflowSpec) and isinstance(ns_, WorkflowSpec)
    out: list[str] = []
    if (os_.workflow_ref or "") != (ns_.workflow_ref or ""):
        out.append(f"workflow_ref changed {os_.workflow_ref!r} -> {ns_.workflow_ref!r}")
    if old.interface is not None and new.interface is not None:
        out += _json_schema_breaking(
            old.interface.input_schema, new.interface.input_schema, "input"
        )
        out += _json_schema_breaking(
            old.interface.output_schema, new.interface.output_schema, "output"
        )
    return out


_BREAKING_MODEL_FIELDS = ("id", "model", "provider", "base_url", "api_key_ref")


def _model_profile_breaking(old: CapabilityManifest, new: CapabilityManifest) -> list[str]:
    """Swapping what the profile points at is major; tuning params is minor."""
    om = old.spec.model  # type: ignore[union-attr]
    nm = new.spec.model  # type: ignore[union-attr]
    return [
        f"{f} changed {getattr(om, f)!r} -> {getattr(nm, f)!r}"
        for f in _BREAKING_MODEL_FIELDS
        if getattr(om, f) != getattr(nm, f)
    ]


def _prompt_breaking(old: CapabilityManifest, new: CapabilityManifest) -> list[str]:
    op = old.spec  # type: ignore[assignment]
    np_ = new.spec  # type: ignore[assignment]
    assert isinstance(op, PromptSpec) and isinstance(np_, PromptSpec)
    out: list[str] = []
    if op.role != np_.role:
        out.append(f"role changed {op.role} -> {np_.role}")
    for v in sorted(set(np_.variables) - set(op.variables)):
        out.append(f"added variable '{v}'")
    return out


def _ref_set_changed(old: list[CapabilityRef], new: list[CapabilityRef]) -> bool:
    return sorted((r.name, r.version) for r in old) != sorted(
        (r.name, r.version) for r in new
    )


def _skill_breaking(old: CapabilityManifest, new: CapabilityManifest) -> list[str]:
    os_ = old.spec  # type: ignore[assignment]
    ns_ = new.spec  # type: ignore[assignment]
    assert isinstance(os_, SkillSpec) and isinstance(ns_, SkillSpec)
    out: list[str] = []
    if os_.prompt_ref != ns_.prompt_ref:
        out.append(
            f"prompt_ref changed {_ref_str(os_.prompt_ref)} -> {_ref_str(ns_.prompt_ref)}"
        )
    if _ref_set_changed(os_.tools, ns_.tools):
        out.append(
            "tool refs changed ("
            + ", ".join(_ref_str(r) for r in os_.tools)
            + ") -> ("
            + ", ".join(_ref_str(r) for r in ns_.tools)
            + ")"
        )
    return out


def _agent_breaking(old: CapabilityManifest, new: CapabilityManifest) -> list[str]:
    oa = old.spec  # type: ignore[assignment]
    na = new.spec  # type: ignore[assignment]
    assert isinstance(oa, AgentSpec) and isinstance(na, AgentSpec)
    out: list[str] = []
    if oa.model_profile != na.model_profile:
        out.append(
            f"model_profile changed {_ref_str(oa.model_profile)} -> {_ref_str(na.model_profile)}"
        )
    if oa.prompt_ref != na.prompt_ref:
        out.append(
            f"prompt_ref changed {_ref_str(oa.prompt_ref)} -> {_ref_str(na.prompt_ref)}"
        )
    if _ref_set_changed(oa.tools, na.tools):
        out.append(
            "tool refs changed ("
            + ", ".join(_ref_str(r) for r in oa.tools)
            + ") -> ("
            + ", ".join(_ref_str(r) for r in na.tools)
            + ")"
        )
    if _ref_set_changed(oa.skills, na.skills):
        out.append(
            "skill refs changed ("
            + ", ".join(_ref_str(r) for r in oa.skills)
            + ") -> ("
            + ", ".join(_ref_str(r) for r in na.skills)
            + ")"
        )
    return out


_BREAKING_CHECKS = {
    CapabilityKind.TOOL: _tool_breaking,
    CapabilityKind.WORKFLOW: _workflow_breaking,
    CapabilityKind.MODEL_PROFILE: _model_profile_breaking,
    CapabilityKind.PROMPT: _prompt_breaking,
    CapabilityKind.SKILL: _skill_breaking,
    CapabilityKind.AGENT: _agent_breaking,
}


def detect_breaking_changes(
    old: CapabilityManifest, new: CapabilityManifest
) -> list[str]:
    """Per-kind breaking changes from `old` to `new` (empty = compatible)."""
    if old.kind != new.kind:
        return [f"kind changed '{old.kind.value}' -> '{new.kind.value}'"]
    check = _BREAKING_CHECKS.get(new.kind)
    if check is None:
        return []  # roadmap kinds have no settled rules yet
    return check(old, new)


# ─── Secret hygiene ──────────────────────────────────────────────────────────

_SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _iter_api_key_refs(manifest: CapabilityManifest) -> list[tuple[str, Optional[str]]]:
    """Every (slot, api_key_ref) pair across the model configs a manifest carries.

    Slots are human-readable paths: 'spec.model' for a model_profile, and
    'spec.workflow.models[i]' for each model embedded in a workflow.
    """
    out: list[tuple[str, Optional[str]]] = []
    spec = manifest.spec
    if isinstance(spec, ModelProfileSpec):
        out.append(("spec.model", spec.model.api_key_ref))
    elif isinstance(spec, WorkflowSpec) and spec.workflow is not None:
        for i, m in enumerate(spec.workflow.models):
            out.append((f"spec.workflow.models[{i}]", m.api_key_ref))
    return out


def _secret_hygiene(manifest: CapabilityManifest) -> list[str]:
    """A model api_key_ref must be a secret *name*, never an embedded key value,
    and any set ref must be declared in secrets_required (referenced-by-name
    contract). Closes the leak of a literal key into a published artifact."""
    errors: list[str] = []
    for slot, ref in _iter_api_key_refs(manifest):
        if not ref:
            continue  # null / local — no secret needed
        if not _SECRET_NAME_RE.match(ref):
            errors.append(
                f"{slot}.api_key_ref looks like an embedded API key value; "
                "store the key in your own secrets and reference it by name"
            )
        elif ref not in manifest.secrets_required:
            errors.append(
                f"{slot}.api_key_ref '{ref}' must be declared in secrets_required"
            )
    return errors


# ─── Entry point ─────────────────────────────────────────────────────────────

async def check_publish(
    db: Database, manifest: CapabilityManifest, batch: Optional[list[CapabilityManifest]] = None
) -> list[str]:
    """Run all publish-time checks. Returns error strings; empty means OK.

    `batch` holds the other manifests being published in the same call (CLI
    multi-publish / seed); refs may be satisfied by batch members, and
    breaking-change/kind-stability baselines include them.
    """
    batch = list(batch or [])
    errors: list[str] = []

    rows = await db.conn.execute_fetchall(
        "SELECT version, kind, manifest_json FROM capability_versions WHERE name=?",
        (manifest.name,),
    )
    candidates: list[CapabilityManifest] = [
        CapabilityManifest.model_validate_json(r["manifest_json"]) for r in rows
    ]
    candidates += [m for m in batch if m.name == manifest.name]

    # 1. Kind stability — a name cannot change kind across versions.
    for c in candidates:
        if c.kind != manifest.kind:
            errors.append(
                f"kind changed from '{c.kind.value}' to '{manifest.kind.value}' "
                f"across versions of {manifest.name} — publish under a new name"
            )
            break

    # 2. Dependency resolution (same semantics as import time).
    errors += await _check_dependencies(db, manifest, batch)

    # 3. Secret hygiene — api_key_ref is a name, never an embedded key value.
    errors += _secret_hygiene(manifest)

    # 4. Breaking changes vs the highest existing lower version.
    new_key = semver_key(manifest.version)
    lowers = [c for c in candidates if semver_key(c.version) < new_key]
    if lowers:
        prev = max(lowers, key=lambda c: semver_key(c.version))
        breaking = detect_breaking_changes(prev, manifest)
        if breaking and semver_key(manifest.version)[0] <= semver_key(prev.version)[0]:
            minimum = f"{semver_key(prev.version)[0] + 1}.0.0"
            errors.append(
                "breaking changes vs "
                f"{prev.name}@{prev.version} without a major bump: "
                + "; ".join(breaking)
                + f" — publish as >= {minimum}"
            )

    return errors
