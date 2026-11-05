"""Local protocol fixture. No network, model, token, or account is used."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


backend = Path(sys.argv[0]).name
args = sys.argv[1:]
prompt = sys.stdin.read()
sessions = Path.cwd() / ".fake-sessions"
sessions.mkdir(exist_ok=True)
resuming = "--resume" in args or "resume" in args
if resuming:
    flag = "--resume" if "--resume" in args else "resume"
    session = args[args.index(flag) + 1]
else:
    session = str(uuid.uuid4())
path = sessions / f"{session}.json"
if resuming and not path.exists():
    print("Unknown native session", file=sys.stderr)
    sys.exit(2)
history = json.loads(path.read_text()) if path.exists() else []
history.append(prompt)
path.write_text(json.dumps(history))
if backend == "codex":
    emit({"type": "thread.started", "thread_id": session})
    emit({"type": "turn.started"})
else:
    emit({"type": "system", "subtype": "init", "session_id": session})
if "MODE=malformed" in prompt:
    print("this is not JSON", flush=True)
    time.sleep(30)
if "MODE=descendant" in prompt:
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
    (Path.cwd() / "child.pid").write_text(str(child.pid))
    time.sleep(60)
delay = re.search(r"SLEEP=([0-9.]+)", prompt)
if delay:
    time.sleep(float(delay[1]))
if "MODE=large" in prompt:
    print("e" * 200000, file=sys.stderr, flush=True)
answer = json.dumps({"turn": len(history), "session": session, "prompts": history, "args": args, "payload": "x" * 200000 if "MODE=large" in prompt else ""}, ensure_ascii=False)
if backend == "codex":
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": answer}})
else:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
if "MODE=partial" not in prompt:
    failed = "MODE=fail" in prompt
    if backend == "codex":
        emit({"type": "turn.failed", "error": {"message": "fixture failure"}} if failed else {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
    else:
        emit({"type": "result", "session_id": session, "subtype": "error_max_turns" if failed else "success", "is_error": failed, "result": answer, "usage": {"input_tokens": 1}})
if "MODE=nonzero" in prompt:
    sys.exit(4)
