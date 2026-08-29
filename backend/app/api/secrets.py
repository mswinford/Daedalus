"""Secrets management API: list, upsert, delete."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.secrets import list_secrets, set_secret, delete_secret

router = APIRouter()


class SecretUpsert(BaseModel):
    name: str
    value: str


@router.get("/secrets")
def secrets_list():
    """List configured secret names (values are never returned)."""
    return list_secrets()


@router.put("/secrets")
def secrets_upsert(body: SecretUpsert):
    """Create or update a secret."""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Secret name cannot be empty")
    set_secret(body.name.strip(), body.value)
    return {"ok": True, "name": body.name.strip()}


@router.delete("/secrets/{name}")
def secrets_delete(name: str):
    """Delete a secret from the file."""
    if not delete_secret(name):
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")
    return {"ok": True}
