# Validation — 5 November 2026

## Executed

```text
DSH_TEST_BIN=/absolute/path/to/dsh python -m pytest -q
31 passed in 9.84s
```

Runtime versions inspected directly:

| Component | Version / evidence |
|---|---|
| Python | 3.12.14 |
| Node.js | 24.22.1 |
| MCP Python SDK | 1.30.0 |
| Harness runtime | `@deepseek-ai/dsh@0.2.2-rc.1`, installed npm distribution |
| Harness config source | [MCP client configuration](../../packages/mcp/mcp-client/README.md), matching runtime `0.2.2-rc.1` |
| Codex CLI | 0.153.4; first-turn and resume argv accepted by the actual CLI's `--help` parser |
| Claude Code CLI | 2.1.318; print/JSON/permission/resume argv accepted by the actual CLI's `--help` parser |

## What the integration test exercised

The installed Harness runs its actual `headless` profile, loads the generated Cordis MCP overlay, negotiates the MCP connection, discovers the worker tools, and executes a scripted sequence through its real model/tool loop:

1. Write shared context.
2. Start the Codex protocol fixture and wait for completion.
3. Send a second task to the same worker and confirm that its native session ID is unchanged.
4. Start the Claude protocol fixture and wait for completion.
5. Complete the coordinator turn.

The coordinator model endpoint is a loopback-only scripted DeepSeek Messages stand-in. The worker executables are subprocess fixtures implementing the documented native JSONL formats and persisted native session history. The test checks actual Harness→MCP→worker process routing; it does not ask a real model to plan or write code.

Additional tests cover official MCP client discovery and reconnect, cleanup on disconnect with a running worker, FIFO turns, session history across server restarts, distinct concurrent workers, optional capacity/deadline settings, shared-context snapshots and revision conflicts, cancellation of queued/running jobs and descendants, failed dependent turns, malformed/partial results, answer phase precedence, large output pagination, reset, exclusive state ownership, and interrupted-task recovery without automatic replay.

## Not verified with a real account

- No paid model API calls were made to OpenAI, Anthropic, or DeepSeek.
- No user login, subscription entitlement, rate limit, or model availability was inspected.
- Live provider behaviour and resumed real native transcripts still need an authenticated smoke on the target machine.
- Runtime checks ran on Linux. The implementation targets macOS/Linux, but this run did not execute macOS Keychain or sandbox behaviour.
- `unrestricted` flag construction was tested and confirmed against CLI help; live unrestricted model execution was not performed.

## Reproduce the Harness integration

Install the package and test dependencies, install `@deepseek-ai/dsh@0.2.2-rc.1` in an isolated directory, then set `DSH_TEST_BIN` to that installation's `dsh` executable:

```bash
DSH_TEST_BIN=/absolute/path/to/node_modules/.bin/dsh .venv/bin/python -m pytest -q
```

Without that environment variable the optional actual-Harness test is skipped; all remaining tests use local fixtures and need no model credentials.

## Authenticated smoke on your machine

After setup and native CLI login, ask Harness to create worker `codex-smoke` with backend `codex` and remember a unique nonce. Wait and read its complete answer. Send a second task to `codex-smoke` asking for the nonce; verify the unchanged session ID. Repeat with `claude-smoke` and backend `claude`. Then write one shared fact and ask both workers to use it in new turns.

Use the small smoke before running larger tasks. It verifies your installed CLI versions, local authentication, native policies, provider access and session persistence together.
