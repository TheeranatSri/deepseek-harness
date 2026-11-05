"""Exercise the generated shell entry point, including workspace and PATH isolation."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from dsh_cli_workers.__main__ import configure


def test_generated_launcher_runs_without_dsh_or_npx_on_path(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("The launcher requires Node.js")
    project = tmp_path / "project with spaces and 'quotes'"
    project.mkdir()
    # A checkout can be a workspace even when its npm executables are unavailable.
    (project / "package.json").write_text(json.dumps({"private": True, "workspaces": ["packages/*"]}))
    output = configure(project, tmp_path / "setup with spaces", "workspace")
    entry = Path(output["launch"]).parent / "runtime/node_modules/@deepseek-ai/dsh/lib/bin.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("console.log(JSON.stringify({cwd:process.cwd(),args:process.argv.slice(2)}));\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("npx", "dsh"):
        stub = tools / name
        stub.write_text("#!/bin/sh\nprintf 'sh: dsh: command not found\\n' >&2\nexit 127\n")
        stub.chmod(0o700)
    environment = dict(os.environ) | {"PATH": str(tools) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(["bash", output["launch"], "--no-open"], env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["cwd"] == str(project)
    assert value["args"] == ["web", "--patch", output["overlay"], "--no-open"]


def test_generated_launcher_reports_a_missing_local_runtime(tmp_path):
    output = configure(tmp_path, tmp_path / "generated", "workspace")
    result = subprocess.run(["bash", output["launch"]], capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "runtime" in result.stderr.lower()
    assert "install.sh" in result.stderr


def test_generated_launcher_opens_the_real_web_help(tmp_path):
    runtime_root = os.environ.get("DSH_TEST_RUNTIME")
    if not runtime_root:
        pytest.skip("Set DSH_TEST_RUNTIME to a directory containing the installed Harness node_modules")
    output = configure(tmp_path, tmp_path / "generated", "workspace")
    runtime = Path(output["launch"]).parent / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "node_modules").symlink_to(Path(runtime_root) / "node_modules", target_is_directory=True)
    environment = dict(os.environ) | {"DSH_HOME": str(tmp_path / "dsh-home")}
    result = subprocess.run(["bash", output["launch"], "--help"], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout
