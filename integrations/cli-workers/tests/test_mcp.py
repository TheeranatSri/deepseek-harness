import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dsh_cli_workers.manager import Manager


async def test_mcp_discovery_context_delegation_and_resume(config, tmp_path):
    config_file = tmp_path / "workers.toml"
    config_file.write_text("\n".join(f"{key} = {json.dumps(str(getattr(config, key)))}" for key in ("workspace", "state_dir", "codex_command", "claude_command")))
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "dsh_cli_workers", "serve", "--config", str(config_file)])
    session_id = None
    # Exercise the same probe-then-serving lifecycle that Harness uses for stdio.
    for probe in (True, False, False):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                assert "resumable" in initialized.instructions
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"worker_submit", "worker_result", "shared_context_set", "worker_events"} <= names
                if probe:
                    continue
                context = await client.call_tool("shared_context_set", {"key": "metric", "value": "WAPE"})
                assert not context.isError
                submitted = await client.call_tool("worker_submit", {"worker": "coder", "backend": "codex", "prompt": "Inspect project"})
                assert not submitted.isError
                job = submitted.structuredContent
                waited = await client.call_tool("worker_wait", {"job_id": job["id"], "seconds": 20})
                assert waited.structuredContent["status"] == "completed"
                result = await client.call_tool("worker_result", {"job_id": job["id"]})
                value = result.structuredContent
                assert "WAPE" in value["text"]
                if session_id is None:
                    session_id = value["native_session"]
                else:
                    assert value["native_session"] == session_id
                    assert json.loads(value["text"])["turn"] == 2


async def test_mcp_disconnect_cleans_up_an_active_worker(config, tmp_path):
    config_file = tmp_path / "workers.toml"
    config_file.write_text("\n".join(f"{key} = {json.dumps(str(getattr(config, key)))}" for key in ("workspace", "state_dir", "codex_command", "claude_command")))
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "dsh_cli_workers", "serve", "--config", str(config_file)])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            job = (await client.call_tool("worker_submit", {"worker": "long-task", "backend": "codex", "prompt": "SLEEP=30"})).structuredContent
            status = await client.call_tool("worker_wait", {"job_id": job["id"], "seconds": 0.1})
            assert status.structuredContent["status"] == "running"
    restored = Manager(config)
    try:
        assert restored.status(job["id"])["status"] == "cancelled"
    finally:
        await restored.close()
