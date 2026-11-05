"""CLI argument construction and authoritative completion parsing."""

from dataclasses import dataclass, field
import json
import uuid

from .config import Config


def command_args(config: Config, backend: str, session_id: str | None, model: str | None) -> list[str]:
    """Use exact session IDs, persistent sessions, JSONL, and stdin prompts."""
    executable = config.command(backend)
    if backend == "codex":
        args = [executable, "exec"]
        # Parent exec options precede resume: resume does not accept --sandbox.
        if config.permission_policy == "unrestricted":
            args += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            args += ["--sandbox", "workspace-write", "-c", 'approval_policy="never"']
        if session_id:
            args += ["resume", session_id]
        args += ["--json", "--skip-git-repo-check"]
        if model:
            args += ["--model", model]
        return args + ["-"]
    if backend == "claude":
        args = [executable, "--print", "--verbose", "--output-format", "stream-json"]
        args += ["--permission-mode", "bypassPermissions" if config.permission_policy == "unrestricted" else "acceptEdits"]
        if session_id:
            args += ["--resume", session_id]
        if model:
            args += ["--model", model]
        return args
    raise ValueError("backend must be codex or claude")


@dataclass
class NativeOutput:
    backend: str
    expected_session: str | None = None
    session_id: str | None = None
    answer: str = ""
    terminal: str | None = None
    error: str | None = None
    usage: dict = field(default_factory=dict)
    answer_priority: int = 0

    def accept(self, event: dict) -> None:
        """Validate process output before recording identity or a completed result."""
        if not isinstance(event, dict):
            raise ValueError("CLI emitted a non-object JSON event")
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise ValueError("CLI event has no string type")
        candidate = event.get("thread_id") if self.backend == "codex" and event_type == "thread.started" else None
        if self.backend == "claude" and event_type in {"system", "result"}:
            candidate = event.get("session_id")
        if candidate is not None:
            if not isinstance(candidate, str):
                raise ValueError("CLI session ID is not a string")
            uuid.UUID(candidate)
            if self.expected_session and candidate != self.expected_session:
                raise ValueError("CLI returned a different session than the requested worker")
            if self.session_id and self.session_id != candidate:
                raise ValueError("CLI changed its session ID during a run")
            self.session_id = candidate
        if self.backend == "codex":
            item = event.get("item", {})
            if event_type == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                if not isinstance(item.get("text"), str):
                    raise ValueError("Codex agent_message has no text")
                phase = item.get("phase")
                priorities = {None: 1, "commentary": 0, "partial_answer": 2, "final_answer": 3}
                if phase not in priorities:
                    raise ValueError("Codex returned an unsupported answer phase")
                priority = priorities[phase]
                if priority and priority >= self.answer_priority:
                    self.answer = item["text"]
                    self.answer_priority = priority
            if event_type in {"turn.completed", "turn.failed"}:
                self.terminal = "completed" if event_type == "turn.completed" else "failed"
                self.usage = event.get("usage", {})
                if event_type == "turn.failed":
                    self.error = json.dumps(event.get("error", "Codex turn failed"), ensure_ascii=False)
        else:
            message = event.get("message", {})
            if event_type == "assistant" and isinstance(message, dict):
                content = message.get("content", [])
                if isinstance(content, list):
                    text = "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
                    if text:
                        self.answer = text
            if event_type == "result":
                success = event.get("subtype") == "success" and event.get("is_error") is False
                self.terminal = "completed" if success else "failed"
                result = event.get("result")
                if isinstance(result, str):
                    self.answer = result
                elif success:
                    self.terminal = "failed"
                    self.error = "Claude returned success without a final result string"
                self.usage = {key: event[key] for key in ("usage", "total_cost_usd", "modelUsage") if key in event}
                if not success:
                    self.error = json.dumps(event.get("errors", event.get("subtype", "Claude run failed")), ensure_ascii=False)

    def completion(self, exit_code: int) -> tuple[str, str | None]:
        """A zero exit code or partial assistant text alone is not success."""
        if exit_code != 0:
            return "failed", self.error or f"CLI exited with status {exit_code}; inspect stderr with worker_log"
        if self.terminal != "completed":
            return "failed", self.error or "CLI exited without an authoritative successful terminal event"
        if not self.session_id:
            return "failed", "CLI completed without a resumable session ID"
        if not self.answer.strip():
            return "failed", "CLI completed without a nonempty final answer"
        return "completed", None
