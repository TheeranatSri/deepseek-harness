"""Operator-owned execution settings. Tools cannot change executable or permissions."""

from dataclasses import dataclass
from pathlib import Path
import math
import shutil
import tomllib


@dataclass(frozen=True)
class Config:
    workspace: Path
    state_dir: Path
    codex_command: str = "codex"
    claude_command: str = "claude"
    permission_policy: str = "workspace"
    max_parallel: int = 0
    task_timeout_seconds: float = 0
    terminate_grace_seconds: float = 3

    def __post_init__(self) -> None:
        if not self.workspace.is_absolute() or not self.workspace.is_dir():
            raise ValueError("workspace must be an existing absolute directory")
        if not self.state_dir.is_absolute():
            raise ValueError("state_dir must be absolute")
        if self.permission_policy not in {"workspace", "unrestricted"}:
            raise ValueError("permission_policy must be workspace or unrestricted")
        if type(self.max_parallel) is not int or self.max_parallel < 0:
            raise ValueError("max_parallel must be a nonnegative integer; 0 means no bridge cap")
        for name in ("task_timeout_seconds", "terminate_grace_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")
        for name in ("codex_command", "claude_command"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be an executable name or path")

    def command(self, backend: str) -> str:
        raw = {"codex": self.codex_command, "claude": self.claude_command}[backend]
        found = shutil.which(raw)
        if found is None:
            raise FileNotFoundError(f"{backend} executable is unavailable; install it or set {backend}_command")
        return found


def load_config(path: Path) -> Config:
    """Load one complete TOML configuration; reject misspelled settings."""
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    data["workspace"] = Path(data["workspace"]).expanduser().resolve()
    data["state_dir"] = Path(data["state_dir"]).expanduser().resolve()
    return Config(**data)
