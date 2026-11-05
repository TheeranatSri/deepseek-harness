"""Durable FIFO turns per worker, parallel workers, and owned process groups."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
import re
import signal
import time
import uuid

from .config import Config
from .native import NativeOutput, command_args
from .store import Store


TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class Manager:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config.state_dir, config.workspace)
        self.tasks: dict[str, asyncio.Task] = {}
        self.worker_locks: dict[str, asyncio.Lock] = {}
        self.capacity = asyncio.Semaphore(config.max_parallel) if config.max_parallel else None
        self.closing = False
        self.logs = config.state_dir / "logs"
        self.logs.mkdir(exist_ok=True, mode=0o700)
        self.logs.chmod(0o700)

    def submit(self, worker: str, prompt: str, backend: str | None = None, model: str | None = None) -> dict:
        """Admit one durable turn. Follow-ups queue behind that worker's active turn."""
        if self.closing:
            raise RuntimeError("Worker server is shutting down")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", worker):
            raise ValueError("Worker name must contain 1-64 letters, digits, underscores or hyphens")
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty")
        if backend is not None and backend not in {"claude", "codex"}:
            raise ValueError("backend must be claude or codex")
        if model is not None and not model.strip():
            raise ValueError("model cannot be empty")
        prior = self.store.db.execute("SELECT * FROM workers WHERE name=?", (worker,)).fetchone()
        if prior is None:
            if backend is None:
                raise ValueError("backend is required when creating a worker")
            self.store.db.execute("INSERT INTO workers(name,backend,created_at) VALUES (?,?,?)", (worker, backend, time.time()))
            self.store.db.commit()
        else:
            if prior["blocked"]:
                raise ValueError("Worker has interrupted work. Inspect native processes and use worker_recover before submitting again")
            if backend is not None and backend != prior["backend"]:
                raise ValueError("A worker's backend cannot change; use a different worker name")
        context = self.store.context()
        # Record the exact context snapshot now: later memory edits do not rewrite queued tasks.
        sent = prompt if not context else (
            "Shared project context supplied by the Harness coordinator (JSON data):\n"
            + json.dumps(context, ensure_ascii=False)
            + "\n\nTask from the Harness coordinator:\n" + prompt
        )
        job = str(uuid.uuid4())
        predecessor = self.store.db.execute("SELECT id FROM jobs WHERE worker=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", (worker,)).fetchone()
        self.store.db.execute("INSERT INTO jobs(id,worker,status,prompt,sent_prompt,model,depends_on,created_at) VALUES (?,?,'queued',?,?,?,?,?)", (job, worker, prompt, sent, model, predecessor[0] if predecessor else None, time.time()))
        self.store.db.commit()
        task = asyncio.create_task(self._run(job), name=f"cli-worker-{job}")
        self.tasks[job] = task
        task.add_done_callback(lambda done: self.tasks.pop(job, None))
        return self.status(job)

    def status(self, job: str) -> dict:
        row = self.store.row("jobs", "id", job)
        return {key: row[key] for key in ("id", "worker", "status", "model", "error", "exit_code", "created_at", "started_at", "ended_at")} | {
            "answer_characters": len(row["answer"]), "usage": json.loads(row["usage"]),
            "native_session": row["native_session"],
        }

    def workers(self) -> list[dict]:
        rows = self.store.db.execute("SELECT * FROM workers ORDER BY created_at")
        return [dict(row) | {"pending_jobs": [job[0] for job in self.store.db.execute("SELECT id FROM jobs WHERE worker=? AND status IN ('queued','running') ORDER BY created_at", (row["name"],))]} for row in rows]

    async def wait(self, job: str, seconds: float = 15) -> dict:
        """Wait for a short polling interval, never cancelling the underlying turn."""
        if not 0 <= seconds <= 20:
            raise ValueError("seconds must be between 0 and 20; this is a poll, not a task deadline")
        self.status(job)
        task = self.tasks.get(job)
        if task is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), seconds)
        return self.status(job)

    def result(self, job: str, offset: int = 0, characters: int = 16000) -> dict:
        if offset < 0 or characters <= 0:
            raise ValueError("offset must be nonnegative and characters must be positive")
        row = self.store.row("jobs", "id", job)
        end = min(len(row["answer"]), offset + characters)
        return self.status(job) | {"text": row["answer"][offset:end], "offset": offset, "next_offset": end if end < len(row["answer"]) else None}

    def events(self, job: str, after: int = 0, count: int = 50) -> dict:
        self.status(job)
        if after < 0 or count < 1:
            raise ValueError("after must be nonnegative and count positive")
        rows = list(self.store.db.execute("SELECT seq,value FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT ?", (job, after, count + 1)))
        return {"events": [{"sequence": row["seq"], "event": json.loads(row["value"])} for row in rows[:count]], "has_more": len(rows) > count, "next_after": rows[min(len(rows), count) - 1]["seq"] if rows else after}

    def log(self, job: str, stream: str = "stderr", offset: int = 0, bytes_count: int = 16000) -> dict:
        self.status(job)
        if stream not in {"stdout", "stderr"} or offset < 0 or bytes_count < 1:
            raise ValueError("Use stdout/stderr, nonnegative offset and positive bytes_count")
        path = self.logs / f"{job}.{stream}.log"
        if not path.exists():
            return {"text": "", "next_offset": None, "total_bytes": 0}
        with path.open("rb") as file:
            file.seek(offset)
            data = file.read(bytes_count)
        total = path.stat().st_size
        return {"text": data.decode("utf-8", errors="replace"), "next_offset": offset + len(data) if offset + len(data) < total else None, "total_bytes": total}

    async def cancel(self, job: str) -> dict:
        row = self.status(job)
        if row["status"] in TERMINAL:
            return row
        task = self.tasks.get(job)
        if task is None:
            raise RuntimeError("Task ownership is missing; restart the server and recover explicitly")
        if not task.cancelling():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # A task cancelled before its first instruction never enters _run's handler.
        if self.status(job)["status"] not in TERMINAL:
            self.store.update("jobs", "id", job, status="cancelled", ended_at=time.time())
        return self.status(job)

    def reset(self, worker: str) -> dict:
        """Start future turns in a new native session, retaining historical jobs."""
        row = self.store.row("workers", "name", worker)
        if row["blocked"]:
            raise ValueError("Interrupted work must be inspected and recovered before reset")
        self._assert_idle(worker)
        self.store.update("workers", "name", worker, native_session=None)
        return self.store.row("workers", "name", worker)

    def recover(self, worker: str, inspected_native_processes: bool) -> dict:
        """Acknowledge an unknown outcome; do not replay or undo the interrupted task."""
        self.store.row("workers", "name", worker)
        self._assert_idle(worker)
        if inspected_native_processes is not True:
            raise ValueError("Inspect native processes and workspace before acknowledging recovery")
        self.store.update("workers", "name", worker, blocked=0)
        return self.store.row("workers", "name", worker)

    def _assert_idle(self, worker: str) -> None:
        if self.store.db.execute("SELECT 1 FROM jobs WHERE worker=? AND status IN ('queued','running')", (worker,)).fetchone():
            raise ValueError("Worker has queued or running work")

    @asynccontextmanager
    async def _slot(self):
        if self.capacity is None:
            yield
        else:
            async with self.capacity:
                yield

    async def _run(self, job: str) -> None:
        row = self.store.row("jobs", "id", job)
        lock = self.worker_locks.setdefault(row["worker"], asyncio.Lock())
        try:
            async with lock, self._slot():
                if row["depends_on"] and self.status(row["depends_on"])["status"] != "completed":
                    self.store.update("jobs", "id", job, status="failed", error="A preceding queued turn did not complete; this dependent turn was not executed", ended_at=time.time())
                    return
                if self.config.task_timeout_seconds:
                    async with asyncio.timeout(self.config.task_timeout_seconds):
                        await self._execute(job)
                else:
                    await self._execute(job)
        except asyncio.CancelledError:
            self.store.update("jobs", "id", job, status="cancelled", ended_at=time.time())
        except TimeoutError:
            self.store.update("jobs", "id", job, status="failed", error="Configured task deadline elapsed; side effects were not rolled back", ended_at=time.time())
        except Exception as error:
            while isinstance(error, ExceptionGroup) and error.exceptions:
                error = error.exceptions[0]
            self.store.update("jobs", "id", job, status="failed", error=str(error), ended_at=time.time())

    async def _execute(self, job: str) -> None:
        row = self.store.row("jobs", "id", job)
        worker = self.store.row("workers", "name", row["worker"])
        args = command_args(self.config, worker["backend"], worker["native_session"], row["model"])
        output = NativeOutput(worker["backend"], expected_session=worker["native_session"])
        self.store.update("jobs", "id", job, status="running", started_at=time.time())
        # Native logins stay native. No token is copied into bridge configuration or logs.
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        process = None
        try:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(*args, cwd=self.config.workspace, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True))
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            async with asyncio.TaskGroup() as group:
                group.create_task(self._read_stdout(job, row["worker"], process.stdout, output))
                group.create_task(self._read_stderr(job, process.stderr))
                group.create_task(self._write_prompt(process, row["sent_prompt"]))
                group.create_task(self._await_exit(process))
            status, error = output.completion(process.returncode)
            self.store.update("jobs", "id", job, status=status, answer=output.answer, error=error, exit_code=process.returncode, usage=json.dumps(output.usage, ensure_ascii=False), ended_at=time.time())
        finally:
            if process is not None:
                await self._stop_process_group(process)

    async def _write_prompt(self, process, prompt: str) -> None:
        try:
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Startup failures are reported by exit status and native terminal events.
        finally:
            process.stdin.close()

    async def _read_stdout(self, job: str, worker: str, stream, output: NativeOutput) -> None:
        pending = b""
        with (self.logs / f"{job}.stdout.log").open("xb", buffering=0) as log:
            while chunk := await stream.read(65536):
                log.write(chunk)
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()
                for line in lines:
                    self._accept_line(job, worker, output, line)
            if pending.strip():
                self._accept_line(job, worker, output, pending)

    def _accept_line(self, job: str, worker: str, output: NativeOutput, line: bytes) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("CLI stdout contained invalid JSON; inspect worker_log") from None
        output.accept(event)
        self.store.event(job, event)
        if output.session_id:
            self.store.update("workers", "name", worker, native_session=output.session_id)
        self.store.update("jobs", "id", job, answer=output.answer, native_session=output.session_id, usage=json.dumps(output.usage, ensure_ascii=False))

    async def _read_stderr(self, job: str, stream) -> None:
        with (self.logs / f"{job}.stderr.log").open("xb", buffering=0) as log:
            while chunk := await stream.read(65536):
                log.write(chunk)

    async def _await_exit(self, process) -> None:
        # returncode is set before inherited pipes close; terminate leftover descendants first.
        while process.returncode is None:
            await asyncio.sleep(0.05)
        await self._stop_process_group(process)

    async def _stop_process_group(self, process) -> None:
        def alive() -> bool:
            try:
                os.killpg(process.pid, 0)
                return True
            except ProcessLookupError:
                return False
        if alive():
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            until = asyncio.get_running_loop().time() + self.config.terminate_grace_seconds
            while alive() and asyncio.get_running_loop().time() < until:
                await asyncio.sleep(0.05)
            if alive():
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    async def close(self) -> None:
        self.closing = True
        pending = list(self.tasks.items())
        for _, task in pending:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        for job, _ in pending:
            if self.status(job)["status"] not in TERMINAL:
                self.store.update("jobs", "id", job, status="cancelled", ended_at=time.time())
        self.store.close()
