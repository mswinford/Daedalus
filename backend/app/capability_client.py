"""Sync client for the capability registry's /use endpoint.

The engine runs in worker threads, so this is a plain httpx (sync) client.
The base URL comes from AI_FORGE_REGISTRY_URL, defaulting to the local
registry port.
"""
import os

import httpx


class CapabilityFetchError(Exception):
    """A capability could not be fetched from the registry."""


class CapabilityClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (
            base_url or os.environ.get("AI_FORGE_REGISTRY_URL", "http://127.0.0.1:3010")
        ).rstrip("/")
        self.timeout = timeout

    def use(self, name: str, version: str = "latest") -> dict:
        """GET /capabilities/{name}/use → {name, version (resolved), kind, stage, artifact, manifest}.

        Raises CapabilityFetchError when the registry is unreachable or the
        capability/version does not exist (or is unpublished).
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/capabilities/{name}/use",
                params={"version": version},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CapabilityFetchError(f"registry unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code == 404:
            raise CapabilityFetchError(
                f"capability '{name}' version '{version}' not found (or unpublished)"
            )
        if resp.status_code >= 400:
            raise CapabilityFetchError(
                f"registry error {resp.status_code} for {name}@{version}: {resp.text[:200]}"
            )
        return resp.json()
