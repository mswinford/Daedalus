"""Permission policies for Copilot sessions.

`safe_only` (default): file writes confined to the node's working directory,
no shell, no URL access — everything else (reads, memory, custom tools) is
approved once. `approve_all`: the SDK's built-in approve-all handler.
"""
import os
from typing import Any


def _is_inside(base: str, path: str) -> bool:
    if not path:
        return False
    base_real = os.path.realpath(base)
    path_real = os.path.realpath(path)
    try:
        return os.path.commonpath([base_real, path_real]) == base_real
    except ValueError:
        return False


def build_permission_handler(policy: str, working_dir: str):
    """Return the SDK permission handler for the given policy."""
    from copilot import PermissionNoResult
    from copilot.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
    from copilot.session import PermissionHandler
    from copilot.session_events import (
        PermissionRequestShell,
        PermissionRequestUrl,
        PermissionRequestWrite,
    )

    if policy == "approve_all":
        return PermissionHandler.approve_all

    async def safe_only(request: Any, invocation: Any):
        if getattr(request, "managed_approval_required", False):
            # Defer to the runtime's managed policy rather than deciding locally.
            return PermissionNoResult()
        match request:
            case PermissionRequestShell():
                return PermissionDecisionReject(
                    feedback="Shell execution is disabled (safe_only permission policy)"
                )
            case PermissionRequestUrl():
                return PermissionDecisionReject(
                    feedback="URL access is disabled (safe_only permission policy)"
                )
            case PermissionRequestWrite(file_name=path):
                if _is_inside(working_dir, path):
                    return PermissionDecisionApproveOnce()
                return PermissionDecisionReject(
                    feedback=f"Write outside the working directory is denied "
                             f"(safe_only permission policy): {path}"
                )
            case _:
                return PermissionDecisionApproveOnce()

    return safe_only
