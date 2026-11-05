import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from dsh_cli_workers.manager import Manager
from dsh_cli_workers.native import NativeOutput, command_args
from dsh_cli_workers.store import Store
from conftest import finish


@pytest.mark.parametrize("backend", ["claude", "codex"])
async def test_followups_resume_the_exact_worker_and_survive_restart(config, backend):
    first = Manager(config)
    job1 = first.submit("research", "remember nonce-lime", backend)
    assert (await finish(first, job1))["status"] == "completed"
    session = first.status(job1["id"])["native_session"]
    await first.close()
    second = Manager(config)
    try:
        job2 = second.submit("research", "recall the nonce")
        assert (await finish(second, job2))["status"] == "completed"
        reply = json.loads(second.result(job2["id"])["text"])
        assert reply["turn"] == 2
        assert reply["session"] == session
        assert "nonce-lime" in reply["prompts"][0]
        assert "--last" not in reply["args"]
    finally:
        await second.close()


async def test_followups_queue_fifo_and_use_the_new_session(manager):
    first = manager.submit("coder", "SLEEP=0.2 first", "codex")
    second = manager.submit("coder", "second")
    assert (await finish(manager, second))["status"] == "completed"
    a, b = manager.status(first["id"]), manager.status(second["id"])
    assert a["native_session"] == b["native_session"]
    assert a["ended_at"] <= b["started_at"]
    assert json.loads(manager.result(second["id"])["text"])["turn"] == 2


async def test_distinct_workers_overlap_without_a_bridge_cap(manager):
    jobs = [manager.submit(f"worker{i}", "SLEEP=0.4", "codex") for i in range(5)]
    results = await asyncio.gather(*(finish(manager, job) for job in jobs))
    assert all(row["status"] == "completed" for row in results)
    assert max(row["started_at"] for row in results) < min(row["ended_at"] for row in results)
    assert len({row["native_session"] for row in results}) == 5


async def test_operator_can_set_capacity(config):
    manager = Manager(replace(config, max_parallel=1))
    try:
        jobs = [manager.submit(f"worker{i}", "SLEEP=0.1", "claude") for i in range(2)]
        results = await asyncio.gather(*(finish(manager, job) for job in jobs))
        assert results[0]["ended_at"] <= results[1]["started_at"]
    finally:
        await manager.close()


async def test_both_backends_receive_the_same_shared_context_snapshot(manager):
    manager.store.set_context("handoff", "Use WAPE and walk-forward validation")
    a = manager.submit("planner", "plan", "claude")
    b = manager.submit("implementer", "implement", "codex")
    manager.store.set_context("handoff", "Changed after submission", expected_revision=1)
    for job in (a, b):
        assert (await finish(manager, job))["status"] == "completed"
        sent = json.loads(manager.result(job["id"])["text"])["prompts"][0]
        assert "Use WAPE" in sent
        assert "Changed after submission" not in sent
    with pytest.raises(ValueError, match="revision conflict"):
        manager.store.set_context("handoff", "stale", expected_revision=1)


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize("mode", ["partial", "fail", "nonzero", "malformed"])
async def test_partial_or_failed_outputs_are_not_success(manager, backend, mode):
    first = manager.submit("worker", f"MODE={mode}", backend)
    dependent = manager.submit("worker", "must not execute after failure")
    assert (await finish(manager, first))["status"] == "failed"
    assert (await finish(manager, dependent))["status"] == "failed"
    assert manager.events(dependent["id"])["events"] == []


async def test_poll_timeout_does_not_cancel_the_turn(manager):
    job = manager.submit("worker", "SLEEP=0.5", "claude")
    row = await manager.wait(job["id"], 0.05)
    assert row["status"] in {"queued", "running"}
    assert (await finish(manager, job))["status"] == "completed"


async def test_queued_cancellation_never_starts_a_native_process(manager):
    a = manager.submit("worker", "SLEEP=0.3", "codex")
    b = manager.submit("worker", "cancel this queued turn")
    assert (await manager.cancel(b["id"]))["status"] == "cancelled"
    assert (await finish(manager, a))["status"] == "completed"
    assert manager.events(b["id"])["events"] == []


async def test_cancellation_terminates_an_owned_descendant_group(manager):
    job = manager.submit("worker", "MODE=descendant", "codex")
    child_file = manager.config.workspace / "child.pid"
    async with asyncio.timeout(5):
        while not child_file.exists():
            await asyncio.sleep(0.02)
    child_pid = int(child_file.read_text())
    assert (await manager.cancel(job["id"]))["status"] == "cancelled"
    # The child can be a zombie waiting for init; it must no longer be executing.
    check = await asyncio.create_subprocess_exec("ps", "-o", "stat=", "-p", str(child_pid), stdout=asyncio.subprocess.PIPE)
    stdout, _ = await check.communicate()
    assert not stdout.strip() or stdout.decode().strip().startswith("Z")


async def test_large_streams_and_answers_are_preserved_and_paginated(manager):
    job = manager.submit("worker", "MODE=large", "codex")
    assert (await finish(manager, job))["status"] == "completed"
    chunks, offset = [], 0
    while True:
        page = manager.result(job["id"], offset, characters=8000)
        chunks.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(json.loads("".join(chunks))["payload"]) == 200000
    assert manager.log(job["id"])["total_bytes"] > 200000
    events = manager.events(job["id"], count=1)
    assert events["has_more"]
    assert manager.events(job["id"], after=events["next_after"])["events"]


async def test_configured_deadline_cancels_native_work(config):
    manager = Manager(replace(config, task_timeout_seconds=0.1))
    try:
        job = manager.submit("worker", "SLEEP=10", "codex")
        row = await finish(manager, job)
        assert row["status"] == "failed"
        assert "deadline" in row["error"]
    finally:
        await manager.close()


async def test_reset_preserves_historical_job_session_ids(manager):
    first = manager.submit("worker", "first", "claude")
    await finish(manager, first)
    prior_id = manager.status(first["id"])["native_session"]
    manager.reset("worker")
    second = manager.submit("worker", "new conversation")
    await finish(manager, second)
    assert manager.status(first["id"])["native_session"] == prior_id
    assert manager.status(second["id"])["native_session"] != prior_id


def test_state_directory_has_one_owner_and_cannot_move_projects(config, tmp_path):
    store = Store(config.state_dir, config.workspace)
    try:
        with pytest.raises(RuntimeError, match="Another worker server"):
            Store(config.state_dir, config.workspace)
    finally:
        store.close()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="different workspace"):
        Store(config.state_dir, other)
    again = Store(config.state_dir, config.workspace)
    again.close()


async def test_crash_recovery_does_not_replay_unknown_work(config):
    original = Manager(config)
    job = original.submit("worker", "initial", "codex")
    await finish(original, job)
    native_id = original.status(job["id"])["native_session"]
    original.store.update("jobs", "id", job["id"], status="running")
    await original.close()
    recovered = Manager(config)
    try:
        assert recovered.status(job["id"])["status"] == "interrupted"
        assert not recovered.tasks
        with pytest.raises(ValueError, match="interrupted work"):
            recovered.submit("worker", "continue")
        with pytest.raises(ValueError, match="Inspect native"):
            recovered.recover("worker", False)
        recovered.recover("worker", True)
        followup = recovered.submit("worker", "continue after inspection")
        assert (await finish(recovered, followup))["native_session"] == native_id
    finally:
        await recovered.close()


def test_permission_modes_and_model_selection_are_operator_owned(config):
    args = command_args(config, "codex", None, "chosen-model")
    assert "workspace-write" in args and "chosen-model" in args
    assert "--ephemeral" not in args
    unrestricted = replace(config, permission_policy="unrestricted")
    assert "--dangerously-bypass-approvals-and-sandbox" in command_args(unrestricted, "codex", None, None)
    assert "bypassPermissions" in command_args(unrestricted, "claude", None, None)
    for backend in ("codex", "claude"):
        args = command_args(config, backend, None, None)
        assert not any(value in args for value in ("--max-turns", "--max-budget-usd", "--no-session-persistence"))


def test_native_parser_rejects_session_mixups():
    output = NativeOutput("codex", expected_session="00000000-0000-0000-0000-000000000001")
    with pytest.raises(ValueError, match="different session"):
        output.accept({"type": "thread.started", "thread_id": "00000000-0000-0000-0000-000000000002"})


def test_codex_commentary_does_not_replace_a_final_answer():
    output = NativeOutput("codex")
    output.accept({"type": "thread.started", "thread_id": "00000000-0000-0000-0000-000000000001"})
    for phase, text in [("final_answer", "Done"), ("commentary", "Checking"), ("partial_answer", "Partial")]:
        output.accept({"type": "item.completed", "item": {"type": "agent_message", "phase": phase, "text": text}})
    output.accept({"type": "turn.completed"})
    assert output.answer == "Done"
    assert output.completion(0)[0] == "completed"


def test_claude_success_requires_a_final_result():
    output = NativeOutput("claude")
    output.accept({"type": "assistant", "message": {"content": [{"type": "text", "text": "Still working"}]}})
    output.accept({"type": "result", "subtype": "success", "is_error": False, "session_id": "00000000-0000-0000-0000-000000000001"})
    assert output.completion(0)[0] == "failed"


async def test_cancelling_a_poll_leaves_the_worker_running(manager):
    job = manager.submit("worker", "SLEEP=0.3", "codex")
    poll = asyncio.create_task(manager.wait(job["id"], 20))
    await asyncio.sleep(0.03)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll
    assert (await finish(manager, job))["status"] == "completed"


async def test_shutdown_does_not_start_queued_turns(config):
    manager = Manager(config)
    first = manager.submit("worker", "SLEEP=30", "codex")
    queued = manager.submit("worker", "must stay unstarted")
    async with asyncio.timeout(5):
        while not manager.events(first["id"])["events"]:
            await asyncio.sleep(0.02)
    await manager.close()
    restored = Manager(config)
    try:
        assert restored.status(first["id"])["status"] == "cancelled"
        assert restored.status(queued["id"])["status"] == "cancelled"
        assert restored.events(queued["id"])["events"] == []
    finally:
        await restored.close()
