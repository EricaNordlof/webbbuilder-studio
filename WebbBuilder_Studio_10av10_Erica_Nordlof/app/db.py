from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(os.getenv("DATABASE_PATH", "./storage/webbbuilder.db"))


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column not in _columns(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_type TEXT NOT NULL,
                stack TEXT NOT NULL,
                brief TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS revisions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                revision_number INTEGER NOT NULL,
                instruction TEXT NOT NULL,
                summary TEXT NOT NULL,
                notes_json TEXT NOT NULL DEFAULT '[]',
                files_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, revision_number)
            );

            CREATE TABLE IF NOT EXISTS deployments (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                deployment_id TEXT,
                service_id TEXT,
                url TEXT,
                commit_sha TEXT,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_revisions_project
            ON revisions(project_id, revision_number DESC);

            CREATE INDEX IF NOT EXISTS idx_deployments_project
            ON deployments(project_id, created_at DESC);
            """
        )

        # Lightweight migrations for older WebbBuilder databases.
        _ensure_column(conn, "projects", "github_repo", "TEXT")
        _ensure_column(conn, "projects", "github_repo_id", "TEXT")
        _ensure_column(conn, "projects", "github_repo_url", "TEXT")
        _ensure_column(conn, "projects", "github_branch", "TEXT DEFAULT 'main'")
        _ensure_column(conn, "projects", "last_commit_sha", "TEXT")
        _ensure_column(conn, "projects", "deploy_provider", "TEXT")
        _ensure_column(conn, "projects", "deploy_service_id", "TEXT")
        _ensure_column(conn, "projects", "live_url", "TEXT")
        _ensure_column(conn, "projects", "auto_publish", "INTEGER NOT NULL DEFAULT 0")


def create_project(
    name: str,
    project_type: str,
    stack: str,
    brief: str,
) -> str:
    project_id = uuid.uuid4().hex[:16]
    now = utcnow()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, project_type, stack, brief,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                project_id,
                name.strip(),
                project_type,
                stack,
                brief.strip(),
                now,
                now,
            ),
        )

    return project_id


def list_projects() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                p.*,
                COALESCE(MAX(r.revision_number), 0) AS revision_count
            FROM projects p
            LEFT JOIN revisions r ON r.project_id = p.id
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_project(project_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()

    return dict(row) if row else None


def update_project(
    project_id: str,
    **fields: Any,
) -> None:
    allowed = {
        "name",
        "project_type",
        "stack",
        "brief",
        "status",
        "github_repo",
        "github_repo_id",
        "github_repo_url",
        "github_branch",
        "last_commit_sha",
        "deploy_provider",
        "deploy_service_id",
        "live_url",
        "auto_publish",
    }

    clean = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not clean:
        return

    clean["updated_at"] = utcnow()

    columns = ", ".join(
        f"{key} = ?"
        for key in clean
    )
    values = list(clean.values()) + [project_id]

    with db() as conn:
        conn.execute(
            f"UPDATE projects SET {columns} WHERE id = ?",
            values,
        )


def delete_project(project_id: str) -> None:
    with db() as conn:
        conn.execute(
            "DELETE FROM projects WHERE id = ?",
            (project_id,),
        )


def next_revision_number(project_id: str) -> int:
    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(revision_number), 0) AS max_rev
            FROM revisions
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()

    return int(row["max_rev"]) + 1


def add_revision(
    project_id: str,
    instruction: str,
    summary: str,
    files: dict[str, str],
    notes: list[str] | None = None,
) -> int:
    revision_number = next_revision_number(project_id)
    now = utcnow()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO revisions (
                id, project_id, revision_number, instruction,
                summary, notes_json, files_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                project_id,
                revision_number,
                instruction.strip(),
                summary.strip(),
                json.dumps(notes or [], ensure_ascii=False),
                json.dumps(files, ensure_ascii=False),
                now,
            ),
        )

        conn.execute(
            """
            UPDATE projects
            SET status = 'generated', updated_at = ?
            WHERE id = ?
            """,
            (now, project_id),
        )

    return revision_number


def list_revisions(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                id, project_id, revision_number,
                instruction, summary, notes_json, created_at
            FROM revisions
            WHERE project_id = ?
            ORDER BY revision_number DESC
            """,
            (project_id,),
        ).fetchall()

    result: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        item["notes"] = json.loads(item.pop("notes_json"))
        result.append(item)

    return result


def get_revision(
    project_id: str,
    revision_number: int,
) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM revisions
            WHERE project_id = ? AND revision_number = ?
            """,
            (project_id, revision_number),
        ).fetchone()

    if not row:
        return None

    item = dict(row)
    item["files"] = json.loads(item.pop("files_json"))
    item["notes"] = json.loads(item.pop("notes_json"))
    return item


def latest_revision(project_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM revisions
            WHERE project_id = ?
            ORDER BY revision_number DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()

    if not row:
        return None

    item = dict(row)
    item["files"] = json.loads(item.pop("files_json"))
    item["notes"] = json.loads(item.pop("notes_json"))
    return item


def add_deployment(
    project_id: str,
    provider: str,
    status: str,
    *,
    deployment_id: str = "",
    service_id: str = "",
    url: str = "",
    commit_sha: str = "",
    message: str = "",
) -> str:
    deployment_row_id = uuid.uuid4().hex

    with db() as conn:
        conn.execute(
            """
            INSERT INTO deployments (
                id, project_id, provider, status,
                deployment_id, service_id, url,
                commit_sha, message, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deployment_row_id,
                project_id,
                provider,
                status,
                deployment_id,
                service_id,
                url,
                commit_sha,
                message,
                utcnow(),
            ),
        )

    return deployment_row_id


def list_deployments(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM deployments
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (project_id,),
        ).fetchall()

    return [dict(row) for row in rows]
