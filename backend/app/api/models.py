"""Model management API: test a model connection without running a workflow."""
import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.secrets import get_secret
from app.engine.llm import create_provider, Message

router = APIRouter(tags=["models"])


class ModelTestRequest(BaseModel):
    provider: str = "openai_compatible"
    model: str
    base_url: Optional[str] = None
    api_key_ref: Optional[str] = None


def _scrub(message: str, api_key: Optional[str]) -> str:
    """Remove the resolved key value from an error message before returning it."""
    if api_key and api_key in message:
        return message.replace(api_key, "****")
    return message


@router.post("/models/test-connection")
async def models_test_connection(req: ModelTestRequest):
    """Probe a model config with a minimal LLM call.

    Secret resolution mirrors builder._build_providers: ``api_key_ref`` is a
    secret name, and a set ref that does not resolve fails loudly. Always
    returns 200 with an ok/message body so the frontend can render inline.
    """
    api_key = None
    if req.api_key_ref:
        api_key = get_secret(req.api_key_ref)
        if api_key is None:
            return {
                "ok": False,
                "message": (
                    f"Model references secret '{req.api_key_ref}', but it is not set. "
                    "Add it in the Secrets panel or export it as an environment variable."
                ),
            }

    try:
        provider = create_provider({
            "provider": req.provider,
            "model": req.model,
            "base_url": req.base_url,
            "api_key": api_key,
        })
    except NotImplementedError:
        return {"ok": False, "message": f"Provider '{req.provider}' is not implemented yet"}
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}

    try:
        await asyncio.wait_for(
            provider.chat([Message(role="user", content="ping")], max_tokens=1),
            timeout=15,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "message": "Timed out after 15s"}
    except Exception as exc:
        return {"ok": False, "message": _scrub(f"Connection failed: {exc}", api_key)}

    return {"ok": True, "model": req.model}
