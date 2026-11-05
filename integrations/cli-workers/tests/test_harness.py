"""Optional actual Harness integration; all model responses and worker CLIs are local fixtures."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

import pytest

from dsh_cli_workers.__main__ import configure


async def test_real_harness_calls_both_workers_and_resumes(config, tmp_path):
    executable = os.environ.get("DSH_TEST_BIN")
    if not executable:
        pytest.skip("Set DSH_TEST_BIN to dsh 0.2.2-rc.1 to test the actual Harness model/tool loop")
    output = configure(config.workspace, tmp_path / "generated", "workspace")
    Path(output["config"]).write_text("\n".join(f"{key} = {json.dumps(str(getattr(config, key)))}" for key in ("workspace", "state_dir", "codex_command", "claude_command")))
    overlay = Path(output["overlay"])
    with overlay.open("a") as file:
        file.write("\n- id: session-title-llm\n  disabled: true\n")
    requests, problems, receipts = [], [], []
    stage = 0
    job = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"data": [{"id": "deepseek-flash", "api_capabilities": {"anthropic_messages": {}}, "context_window": 1000000, "max_output_tokens": 8192}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            nonlocal stage, job
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            tools = {tool.get("name") for tool in body.get("tools", [])}
            if not any(name and name.startswith("mcp__cli_workers__") for name in tools):
                problems.append("Harness did not expose the configured MCP tools")
            if stage:
                found = []
                for message in body.get("messages", []):
                    for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                        if block.get("type") == "tool_result":
                            found.append(block)
                if found:
                    last = found[-1]
                    if last.get("is_error"):
                        problems.append(str(last))
                    content = last.get("content", [])
                    text = content if isinstance(content, str) else "".join(part.get("text", "") for part in content if part.get("type") == "text")
                    try:
                        receipt = json.loads(text)
                        receipts.append(receipt)
                        job = receipt.get("id", job)
                    except (json.JSONDecodeError, AttributeError):
                        problems.append("Could not read MCP result JSON: " + text[:300])
            calls = [
                ("shared_context_set", {"key": "metric", "value": "WAPE"}),
                ("worker_submit", {"worker": "coder", "backend": "codex", "prompt": "First turn"}),
                ("worker_wait", {"job_id": job, "seconds": 20}),
                ("worker_submit", {"worker": "coder", "prompt": "Second turn"}),
                ("worker_wait", {"job_id": job, "seconds": 20}),
                ("worker_submit", {"worker": "planner", "backend": "claude", "prompt": "Review context"}),
                ("worker_wait", {"job_id": job, "seconds": 20}),
            ]
            events = [{"type": "message_start", "message": {"id": f"msg_{stage}", "model": body.get("model", "deepseek-flash"), "usage": {"input_tokens": 1, "output_tokens": 0}}}]
            if stage < len(calls) and not problems:
                name, arguments = calls[stage]
                events += [
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": f"call_{stage}", "name": "mcp__cli_workers__" + name, "input": arguments}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
                ]
            else:
                events += [
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "HARNESS_CLI_WORKERS_OK"}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                ]
            events.append({"type": "message_stop"})
            payload = "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode()
            stage += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    # These are test-only dummy credentials for a loopback-only fixture endpoint.
    environment = dict(os.environ) | {"DSH_HOME": str(tmp_path / "dsh-home"), "DEEPSEEK_API_KEY": "local-fixture-only", "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{http.server_port}", "NO_PROXY": "127.0.0.1,localhost"}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(executable, "--profile", "headless", "--patch", str(overlay), "--approval-policy", "never", "Validate local CLI workers", cwd=config.workspace, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
        assert process.returncode == 0, stderr.decode()[-5000:]
        assert not problems, problems
        assert b"HARNESS_CLI_WORKERS_OK" in stdout
        completed = [row for row in receipts if row.get("status") == "completed"]
        assert len(completed) == 3, (receipts, stderr.decode()[-2000:])
        assert completed[0]["native_session"] == completed[1]["native_session"]
        assert completed[2]["worker"] == "planner"
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
