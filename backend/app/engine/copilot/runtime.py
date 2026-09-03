"""Copilot SDK runtime seam.

`CopilotRuntime` is the single interface the copilot_agent node depends on.
The real implementation (`SdkCopilotRuntime`) wraps github-copilot-sdk's
asyncio client — one client (and one runtime process) per call, started and
stopped inside `run_task`. Tests substitute a fake implementing the same
method; nothing else in the engine imports the SDK directly.

Process model: option (a) per-run stdio client — see docs/ai-forge-plan.md
(copilot section). The seam exists so a shared external TCP server can be
swapped in later without touching the node handler.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


class CopilotRuntimeError(Exception):
    """Base error for copilot runtime failures."""


class CopilotNoResponseError(CopilotRuntimeError):
    """Session went idle without producing an assistant message.

    The typical cause is missing GitHub auth or no active Copilot
    subscription — the runtime then goes idle silently instead of raising.
    """


class CopilotTimeoutError(CopilotRuntimeError):
    """The session exceeded its wall-clock cap."""


class CopilotSessionError(CopilotRuntimeError):
    """The runtime reported a session error (auth, model, rate limit, ...)."""

    def __init__(self, error_type: str, message: str, status_code: Optional[int] = None):
        super().__init__(f"{error_type}: {message}")
        self.error_type = error_type
        self.status_code = status_code


@dataclass
class ToolCallRecord:
    id: str
    name: str
    args: Any = None
    success: Optional[bool] = None
    error: Optional[str] = None


@dataclass
class CopilotResult:
    final_message: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    model: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: Optional[float] = None


# (kind, payload) callback — kind is "tool_call" or "tool_result".
EventCallback = Callable[[str, dict], None]


class CopilotRuntime(Protocol):
    async def run_task(
        self,
        *,
        task: str,
        model: Optional[str] = None,
        working_dir: str,
        permission_policy: str = "safe_only",
        timeout_seconds: Optional[int] = None,
        github_token: Optional[str] = None,
        on_event: Optional[EventCallback] = None,
    ) -> CopilotResult: ...


def create_copilot_runtime() -> CopilotRuntime:
    return SdkCopilotRuntime()


class SdkCopilotRuntime:
    """Real implementation over github-copilot-sdk (lazy import — optional dep)."""

    async def run_task(
        self,
        *,
        task: str,
        model: Optional[str] = None,
        working_dir: str,
        permission_policy: str = "safe_only",
        timeout_seconds: Optional[int] = None,
        github_token: Optional[str] = None,
        on_event: Optional[EventCallback] = None,
    ) -> CopilotResult:
        try:
            from copilot import CopilotClient
            from copilot.session_events import (
                AssistantMessageData,
                AssistantUsageData,
                SessionErrorData,
                SessionIdleData,
                ToolExecutionCompleteData,
                ToolExecutionStartData,
            )
        except ImportError as e:
            raise CopilotRuntimeError(
                "github-copilot-sdk is not installed — install with `pip install ai-forge[copilot]`"
            ) from e

        from app.engine.copilot.permissions import build_permission_handler

        client = CopilotClient(working_directory=working_dir, github_token=github_token)
        await client.start()
        try:
            kwargs: dict[str, Any] = {
                "on_permission_request": build_permission_handler(permission_policy, working_dir),
            }
            if model:
                kwargs["model"] = model
            session = await client.create_session(**kwargs)

            result = CopilotResult(final_message="")
            tool_records: dict[str, ToolCallRecord] = {}
            error_info: dict[str, Any] = {}
            done = asyncio.Event()

            def on_session_event(event: Any) -> None:
                d = event.data
                match d:
                    case AssistantMessageData(content=content) if content:
                        result.final_message = content
                    case ToolExecutionStartData(tool_call_id=tcid, tool_name=name, arguments=args):
                        rec = ToolCallRecord(id=tcid, name=name, args=args)
                        tool_records[tcid] = rec
                        result.tool_calls.append(rec)
                        if on_event:
                            on_event("tool_call", {"name": name, "args": args})
                    case ToolExecutionCompleteData(tool_call_id=tcid, success=ok, error=err):
                        rec = tool_records.get(tcid)
                        if rec is not None:
                            rec.success = ok
                            rec.error = getattr(err, "message", None) or (str(err) if err else None)
                        if on_event:
                            on_event("tool_result", {
                                "name": rec.name if rec else None,
                                "success": ok,
                            })
                    case AssistantUsageData(model=m, input_tokens=ti, output_tokens=to, cost=c):
                        if m:
                            result.model = m
                        result.tokens_input += ti or 0
                        result.tokens_output += to or 0
                        if c:
                            result.cost_usd = (result.cost_usd or 0.0) + c
                    case SessionErrorData(error_type=et, message=msg, status_code=sc):
                        error_info.update({"error_type": et, "message": msg, "status_code": sc})
                    case SessionIdleData():
                        done.set()

            session.on(on_session_event)
            await session.send(task)
            try:
                if timeout_seconds is not None:
                    await asyncio.wait_for(done.wait(), timeout=timeout_seconds)
                else:
                    await done.wait()
            except TimeoutError:
                raise CopilotTimeoutError(
                    f"Copilot session exceeded {timeout_seconds}s"
                ) from None

            if error_info:
                raise CopilotSessionError(
                    error_info["error_type"], error_info["message"],
                    error_info.get("status_code"),
                )
            if not result.final_message.strip():
                raise CopilotNoResponseError(
                    "Copilot runtime returned no response — check GitHub auth and Copilot subscription"
                )
            return result
        except CopilotRuntimeError:
            raise
        except Exception as e:
            raise CopilotRuntimeError(f"Copilot runtime failed: {e}") from e
        finally:
            # stop() raises (ExceptionGroup) when the child already died —
            # that is a successful cleanup, not an error (spike finding).
            try:
                await client.stop()
            except Exception:
                pass
