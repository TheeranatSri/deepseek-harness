"""SQLite state, shared context, and append-only native events for one workspace."""

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, directory: Path, workspace: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.directory = directory
        self.lock = (directory / "server.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Another worker server owns this state directory") from None
        self.db = sqlite3.connect(directory / "state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workers (
                name TEXT PRIMARY KEY, backend TEXT NOT NULL, native_session TEXT,
                blocked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, worker TEXT NOT NULL REFERENCES workers(name),
                status TEXT NOT NULL, prompt TEXT NOT NULL, sent_prompt TEXT NOT NULL,
                model TEXT, answer TEXT NOT NULL DEFAULT '', error TEXT, exit_code INTEGER,
                usage TEXT NOT NULL DEFAULT '{}', native_session TEXT,
                depends_on TEXT REFERENCES jobs(id), created_at REAL NOT NULL,
                started_at REAL, ended_at REAL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL REFERENCES jobs(id),
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, revision INTEGER NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        prior = self.db.execute("SELECT value FROM meta WHERE key='workspace'").fetchone()
        if prior is not None and prior["value"] != str(workspace):
            self.close()
            raise ValueError("State directory belongs to a different workspace")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('workspace', ?)", (str(workspace),))
        version = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if version is not None and version["value"] != "1":
            self.close()
            raise ValueError("Unsupported worker state version")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', '1')")
        # Never replay a task whose side effects may already have happened.
        self.db.execute("UPDATE workers SET blocked=1 WHERE name IN (SELECT worker FROM jobs WHERE status IN ('queued','running'))")
        self.db.execute("UPDATE jobs SET status='interrupted', error='Server stopped before task completion. Inspect native processes and workspace before explicit recovery.', ended_at=? WHERE status IN ('queued','running')", (time.time(),))
        self.db.commit()
        os.chmod(directory / "state.sqlite3", 0o600)

    def close(self) -> None:
        self.db.close()
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def row(self, table: str, key: str, value: str) -> dict:
        # Table and column names are internal constants, never tool input.
        row = self.db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown {table.rstrip('s')}")
        return dict(row)

    def update(self, table: str, key: str, value: str, **fields) -> None:
        columns = ", ".join(f"{name}=?" for name in fields)
        self.db.execute(f"UPDATE {table} SET {columns} WHERE {key}=?", (*fields.values(), value))
        self.db.commit()

    def context(self) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM context ORDER BY key")]

    def set_context(self, key: str, value: str, expected_revision: int | None = None) -> dict:
        if not key.strip():
            raise ValueError("Context key cannot be empty")
        prior = self.db.execute("SELECT revision FROM context WHERE key=?", (key,)).fetchone()
        revision = prior[0] if prior else 0
        if expected_revision is not None and expected_revision != revision:
            raise ValueError(f"Context revision conflict: expected {expected_revision}, current {revision}")
        self.db.execute("INSERT INTO context VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, revision=excluded.revision, updated_at=excluded.updated_at", (key, value, revision + 1, time.time()))
        self.db.commit()
        return self.row("context", "key", key)

    def event(self, job: str, event: dict) -> None:
        self.db.execute("INSERT INTO events (job,value) VALUES (?,?)", (job, json.dumps(event, ensure_ascii=False)))
        self.db.commit()
