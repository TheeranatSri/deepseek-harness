"""Configure a Harness MCP overlay, inspect local CLI installs, or serve workers."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from .config import load_config


HARNESS_RUNTIME_VERSION = "0.2.2-rc.1"


def configure(workspace: Path, output_dir: Path, policy: str) -> dict:
    workspace = workspace.expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("Workspace must be a directory")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scope = hashlib.sha256(str(workspace).encode()).hexdigest()[:16]
    state = Path.home() / ".local" / "share" / "dsh-cli-workers" / scope
    q = json.dumps  # JSON strings are also valid TOML/YAML double-quoted scalars.
    config = output_dir / "workers.toml"
    config.write_text("\n".join([
        f"workspace = {q(str(workspace))}", f"state_dir = {q(str(state))}",
        f"codex_command = {q(shutil.which('codex') or 'codex')}",
        f"claude_command = {q(shutil.which('claude') or 'claude')}",
        f"permission_policy = {q(policy)}", "max_parallel = 0", "task_timeout_seconds = 0",
        "terminate_grace_seconds = 3", "",
    ]), encoding="utf-8")
    config.chmod(0o600)
    overlay = output_dir / "workers.cordis.yml"
    overlay.write_text("\n".join([
        "- insert:", "    - id: cli-workers", "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:", "        serverName: cli_workers", "        transport: stdio",
        f"        command: {q(sys.executable)}", f"        args: {q(['-m', 'dsh_cli_workers', 'serve', '--config', str(config)])}",
        f"        cwd: {q(str(workspace))}", "        toolCallTimeoutMs: 30000", "        startupTimeoutMs: 30000",
        "        closeTimeoutMs: 15000", "        failOnStartupError: true",
        "        env: !!js |",
        "          Object.fromEntries(['PATH', 'HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'].filter(k => process.env[k] !== undefined).map(k => [k, process.env[k]]))",
        "",
    ]), encoding="utf-8")
    runtime = output_dir / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "package.json").write_text(json.dumps({
        "name": "dsh-cli-workers-runtime", "version": "0.0.0", "private": True,
        "type": "module", "engines": {"node": ">=24.9"},
        "dependencies": {"@deepseek-ai/dsh": HARNESS_RUNTIME_VERSION},
    }, indent=2) + "\n", encoding="utf-8")
    entrypoint = runtime / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    launcher = output_dir / "launch.sh"
    launcher.write_text("\n".join([
        "#!/usr/bin/env bash", "set -euo pipefail",
        f"task_entry={shlex.quote(str(entrypoint))}",
        'if [[ ! -f "$task_entry" ]]; then',
        "  printf '%s\\n' 'Harness runtime is missing. Run install.sh again to install it.' >&2",
        "  exit 1", "fi", f"cd -- {shlex.quote(str(workspace))}",
        f'exec node "$task_entry" web --patch {shlex.quote(str(overlay))} "$@"', "",
    ]), encoding="utf-8")
    launcher.chmod(0o700)
    return {"config": str(config), "overlay": str(overlay), "launch": str(launcher), "runtime": str(runtime), "permission_policy": policy}


def doctor(config_path: Path) -> list[dict]:
    config = load_config(config_path)
    result = []
    for backend in ("codex", "claude"):
        try:
            command = config.command(backend)
            check = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=15)
            result.append({"backend": backend, "command": command, "version": check.stdout.strip(), "ok": check.returncode == 0})
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            result.append({"backend": backend, "ok": False, "error": str(error)})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    setup = commands.add_parser("configure")
    setup.add_argument("--workspace", type=Path, required=True)
    setup.add_argument("--output-dir", type=Path, default=Path.cwd() / "local")
    setup.add_argument("--policy", choices=["workspace", "unrestricted"], default="workspace")
    for action in ("serve", "doctor"):
        command = commands.add_parser(action)
        command.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "configure":
        print(json.dumps(configure(args.workspace, args.output_dir, args.policy), ensure_ascii=False, indent=2))
    elif args.action == "doctor":
        print(json.dumps(doctor(args.config), ensure_ascii=False, indent=2))
    else:
        from .server import create_server
        create_server(load_config(args.config)).run(transport="stdio")


if __name__ == "__main__":
    main()
