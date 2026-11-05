"""Official MCP SDK transport; Harness owns orchestration through these tools."""

from contextlib import asynccontextmanager
from typing import Any, Literal

from anyio import CancelScope
from mcp.server.fastmcp import FastMCP

from .config import Config
from .manager import Manager


INSTRUCTIONS = """You control resumable Claude Code and Codex CLI workers in one fixed project.
Use worker_submit with a backend for a new named worker. Reuse its name for follow-up turns;
the exact native session is resumed, never the most recent unrelated session. Each worker's
turns run FIFO. Distinct workers can run concurrently. Poll worker_wait until a terminal
status, then read worker_result, following every next_offset until null. Polls finish in
20 seconds or less; jobs themselves have no default deadline or turn/budget cap.
Use shared_context_set for project facts and handoffs. Each submitted job receives an exact
snapshot of the shared context. The workers do not automatically share their private chats.
Use worker_events for native tool/command progress and worker_log for explicit diagnostics.
Do not claim success for failed, cancelled or interrupted jobs or automatically retry them:
side effects may have occurred. On interrupted work inspect native processes and workspace
before explicitly calling worker_recover. Use separate git worktrees/projects for concurrent
edits to the same files. This server does not raise provider quotas or model context limits.
"""


def create_server(config: Config) -> FastMCP:
    manager: Manager | None = None

    @asynccontextmanager
    async def lifespan(server):
        nonlocal manager
        manager = Manager(config)
        try:
            yield {}
        finally:
            with CancelScope(shield=True):
                await manager.close()

    server = FastMCP("dsh-cli-workers", instructions=INSTRUCTIONS, lifespan=lifespan)

    def current() -> Manager:
        if manager is None:
            raise RuntimeError("Worker server has not started")
        return manager

    @server.tool()
    async def worker_submit(worker: str, prompt: str, backend: Literal["claude", "codex"] | None = None, model: str | None = None) -> dict[str, Any]:
        """Start or queue a turn. First use requires backend; later uses resume the same worker."""
        return current().submit(worker, prompt, backend, model)

    @server.tool()
    async def worker_list() -> dict[str, Any]:
        """List workers, native session IDs, recovery state, and pending jobs."""
        return {"workers": current().workers(), "permission_policy": config.permission_policy, "max_parallel": config.max_parallel, "task_timeout_seconds": config.task_timeout_seconds}

    @server.tool()
    async def worker_status(job_id: str) -> dict[str, Any]:
        """Get one job's status, timing, error, and native usage metadata."""
        return current().status(job_id)

    @server.tool()
    async def worker_wait(job_id: str, seconds: float = 15) -> dict[str, Any]:
        """Poll for up to 20 seconds. A poll timeout does not cancel or time-limit the job."""
        return await current().wait(job_id, seconds)

    @server.tool()
    async def worker_result(job_id: str, offset: int = 0, characters: int = 16000) -> dict[str, Any]:
        """Read saved answer text. Follow next_offset for remaining text; storage is untruncated."""
        return current().result(job_id, offset, characters)

    @server.tool()
    async def worker_events(job_id: str, after_sequence: int = 0, count: int = 50) -> dict[str, Any]:
        """Read native JSONL events, including commands and tool activity; follow next_after."""
        return current().events(job_id, after_sequence, count)

    @server.tool()
    async def worker_log(job_id: str, stream: Literal["stdout", "stderr"] = "stderr", offset: int = 0, bytes_count: int = 16000) -> dict[str, Any]:
        """Explicitly inspect raw local CLI diagnostics. These logs can contain project data."""
        return current().log(job_id, stream, offset, bytes_count)

    @server.tool()
    async def worker_cancel(job_id: str) -> dict[str, Any]:
        """Cancel this exact queued/running job and terminate its owned native process group."""
        return await current().cancel(job_id)

    @server.tool()
    async def worker_reset(worker: str) -> dict[str, Any]:
        """Clear an idle worker's native session for its next task; retain historical results."""
        return current().reset(worker)

    @server.tool()
    async def worker_recover(worker: str, inspected_native_processes: bool) -> dict[str, Any]:
        """After a crash, acknowledge manual inspection before resuming a blocked worker."""
        return current().recover(worker, inspected_native_processes)

    @server.tool()
    async def shared_context_get() -> dict[str, Any]:
        """Read shared project facts with revisions. Both backends receive these on submission."""
        return {"entries": current().store.context()}

    @server.tool()
    async def shared_context_set(key: str, value: str, expected_revision: int | None = None) -> dict[str, Any]:
        """Write a project fact/handoff. Optional expected_revision prevents overwriting stale data."""
        return current().store.set_context(key, value, expected_revision)

    return server
