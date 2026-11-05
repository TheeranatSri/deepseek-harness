from dataclasses import replace
from pathlib import Path
import sys

import pytest

from dsh_cli_workers.config import Config
from dsh_cli_workers.manager import Manager


@pytest.fixture
def config(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    scripts = tmp_path / "bin"
    scripts.mkdir()
    fixture = Path(__file__).with_name("fake_cli.py").read_text()
    for backend in ("codex", "claude"):
        path = scripts / backend
        path.write_text(f"#!{sys.executable}\n" + fixture)
        path.chmod(0o700)
    return Config(workspace=project, state_dir=tmp_path / "state", codex_command=str(scripts / "codex"), claude_command=str(scripts / "claude"), terminate_grace_seconds=0.1)


@pytest.fixture
async def manager(config):
    value = Manager(config)
    try:
        yield value
    finally:
        await value.close()


async def finish(manager, job):
    return await manager.wait(job["id"], 20)
