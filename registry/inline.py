"""Inline capability references into self-contained artifacts (R1 import).

R1 imports are inline: a consumed artifact must be self-contained, with no
live references resolved at runtime. skill and agent specs reference other
capabilities by name@version, so the inliner resolves those refs against the
registry and embeds the concrete payloads (tool definitions, prompt text,
model profiles, nested skills).

The ref graph is structurally acyclic: only agent -> {skill, tool,
model_profile, prompt} and skill -> {tool, prompt} edges exist, and tools /
prompts / model profiles carry no refs of their own.
"""
from typing import Any

from registry.db import Database
from registry.store import get_artifact
from schema.capability import (
    AgentSpec,
    CapabilityManifest,
    CapabilityRef,
    SkillSpec,
)


class InliningError(Exception):
    """A capability ref could not be resolved to a usable payload."""


async def _artifact_for(
    db: Database, name: str, version: str, want_kind: str
) -> dict[str, Any]:
    try:
        art = await get_artifact(db, name, version)
    except (KeyError, LookupError) as e:
        raise InliningError(str(e)) from e
    if art["kind"] != want_kind:
        raise InliningError(
            f"{name}@{version} is kind '{art['kind']}', expected '{want_kind}'"
        )
    return art


async def _resolve_prompt_text(db: Database, name: str, version: str) -> str:
    art = await _artifact_for(db, name, version, "prompt")
    return art["artifact"]["text"]


async def _resolve_tool(db: Database, name: str, version: str) -> dict[str, Any]:
    art = await _artifact_for(db, name, version, "tool")
    return art["artifact"]  # ToolDefinition JSON


async def _inline_skill_spec(
    db: Database, spec: SkillSpec, display_name: str
) -> dict[str, Any]:
    """skill -> {name, prompt, tools: [ToolDefinition...]}"""
    prompt = spec.prompt
    if prompt is None and spec.prompt_ref is not None:
        prompt = await _resolve_prompt_text(
            db, spec.prompt_ref.name, spec.prompt_ref.version
        )
    tools = [
        await _resolve_tool(db, t.name, t.version) for t in spec.tools
    ]
    return {"name": display_name, "prompt": prompt or "", "tools": tools}


async def _resolve_skill(
    db: Database, ref: CapabilityRef
) -> dict[str, Any]:
    """Fetch a skill capability by ref and inline its spec."""
    art = await _artifact_for(db, ref.name, ref.version, "skill")
    spec = SkillSpec.model_validate(art["manifest"]["spec"])
    return await _inline_skill_spec(db, spec, ref.name)


async def inline_artifact(
    db: Database, name: str, version: str = "latest"
) -> dict[str, Any]:
    """The consumable payload with all capability refs inlined.

    tool / model_profile / prompt / workflow artifacts are already
    self-contained and pass through unchanged. skill / agent artifacts get
    their refs resolved:
      skill -> {name, prompt, tools}
      agent -> {model, prompt, tools, skills}
    """
    base = await get_artifact(db, name, version)
    manifest = CapabilityManifest.model_validate(base["manifest"])
    spec = manifest.spec

    if isinstance(spec, SkillSpec):
        return {**base, "artifact": await _inline_skill_spec(db, spec, name)}

    if isinstance(spec, AgentSpec):
        model = (
            await _artifact_for(
                db, spec.model_profile.name, spec.model_profile.version,
                "model_profile",
            )
        )["artifact"]  # ModelConfig JSON
        prompt = spec.prompt
        if prompt is None and spec.prompt_ref is not None:
            prompt = await _resolve_prompt_text(
                db, spec.prompt_ref.name, spec.prompt_ref.version
            )
        tools = [await _resolve_tool(db, t.name, t.version) for t in spec.tools]
        skills = [await _resolve_skill(db, s) for s in spec.skills]
        return {
            **base,
            "artifact": {
                "model": model,
                "prompt": prompt or "",
                "tools": tools,
                "skills": skills,
            },
        }

    return base
