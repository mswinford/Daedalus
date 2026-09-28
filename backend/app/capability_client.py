"""Sync client for the capability registry's /use endpoint.

The engine runs in worker threads, so this is a plain httpx (sync) client.
The base URL comes from DAEDALUS_REGISTRY_URL, defaulting to the local
registry port.
"""
import os

import httpx


class CapabilityFetchError(Exception):
    """A capability could not be fetched from the registry."""


class CapabilityNotFoundError(CapabilityFetchError):
    """The requested capability/version does not exist (or is unpublished).

    Permanent, unlike a generic fetch error (registry unreachable) — callers
    that rebuild checkpointed graphs can fail loudly instead of retrying.
    """


class CapabilityClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (
            base_url or os.environ.get("DAEDALUS_REGISTRY_URL", "http://127.0.0.1:3010")
        ).rstrip("/")
        self.timeout = timeout

    def use(self, name: str, version: str = "latest", inline: bool = False) -> dict:
        """GET /capabilities/{name}/use?version=...[&inline=true] → {version, kind, artifact}.

        With inline=True, composite artifacts (skill/agent) come back with all
        capability refs resolved into self-contained payloads (registry/inline.py).

        Raises CapabilityNotFoundError for 404 (unknown capability/version or
        unpublished) and CapabilityFetchError when the registry is unreachable.
        """
        params = {"version": version}
        if inline:
            params["inline"] = "true"
        try:
            resp = httpx.get(
                f"{self.base_url}/registry/capabilities/{name}/use",
                params=params,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CapabilityFetchError(f"registry unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code == 404:
            raise CapabilityNotFoundError(
                f"capability '{name}' version '{version}' not found (or unpublished)"
            )
        if resp.status_code >= 400:
            raise CapabilityFetchError(
                f"registry error {resp.status_code} for {name}@{version}: {resp.text[:200]}"
            )
        return resp.json()

    def list_versions(self, name: str) -> list[dict]:
        """GET /capabilities/{name} → all versions (newest first), each with
        version/kind/stage/... metadata.

        Raises CapabilityNotFoundError for an unknown capability (404) and
        CapabilityFetchError when the registry is unreachable — same contract
        as use().
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/registry/capabilities/{name}",
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CapabilityFetchError(f"registry unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code == 404:
            raise CapabilityNotFoundError(f"capability '{name}' not found")
        if resp.status_code >= 400:
            raise CapabilityFetchError(
                f"registry error {resp.status_code} for {name}: {resp.text[:200]}"
            )
        return resp.json().get("versions", [])

    def write_evaluation(self, name: str, version: str, payload: dict) -> dict:
        """PUT /capabilities/{name}/versions/{version}/evaluation → the registry's response.

        Raises CapabilityNotFoundError for an unknown capability/version (404)
        and CapabilityFetchError when the registry is unreachable or returns
        any other error — same contract as use().
        """
        try:
            resp = httpx.put(
                f"{self.base_url}/registry/capabilities/{name}/versions/{version}/evaluation",
                json=payload,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise CapabilityFetchError(f"registry unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code == 404:
            raise CapabilityNotFoundError(
                f"capability '{name}' version '{version}' not found (or unpublished)"
            )
        if resp.status_code >= 400:
            raise CapabilityFetchError(
                f"registry error {resp.status_code} for evaluation {name}@{version}: {resp.text[:200]}"
            )
        return resp.json()
