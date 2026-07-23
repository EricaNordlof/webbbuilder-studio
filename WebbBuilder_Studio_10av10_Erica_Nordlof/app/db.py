from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

DB_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        "./storage/webbbuilder.db",
    )
)

USE_POSTGRES = DATABASE_URL.startswith(
    (
        "postgres://",
        "postgresql://",
    )
)


def utcnow() -> str:
    """
    Returnerar aktuell UTC-tid som ISO-8601-sträng.
    Samma format används i både SQLite och PostgreSQL.
    """
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# CONNECTION
# ============================================================

def connect() -> Any:
    """
    Ansluter till PostgreSQL när DATABASE_URL finns.

    Annars används lokal SQLite-databas som fallback,
    exempelvis vid lokal utveckling.
    """

    if USE_POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL är aktiverat via DATABASE_URL, "
                "men Python-paketet 'psycopg' saknas. "
                "Lägg till 'psycopg[binary]' i requirements.txt."
            ) from exc

        database_url = DATABASE_URL

        # Normalisera äldre postgres://-URL:er.
        if database_url.startswith("postgres://"):
            database_url = (
                "postgresql://"
                + database_url[len("postgres://"):]
            )

        return psycopg.connect(
            database_url,
            row_factory=dict_row,
            connect_timeout=10,
        )

    # Lokal SQLite fallback.
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    # Förbättrar samtidig läsning/skrivning lokalt.
    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return conn


@contextmanager
def db() -> Iterator[Any]:
    """
    Gemensam transaktionshantering för SQLite och PostgreSQL.
    """

    conn = connect()

    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# SQL HELPERS
# ============================================================

def _sql(query: str) -> str:
    """
    SQLite använder ? som placeholder.
    Psycopg/PostgreSQL använder %s.

    Våra interna queries använder därför ? och konverteras
    automatiskt när PostgreSQL används.
    """

    if USE_POSTGRES:
        return query.replace("?", "%s")

    return query


def _execute(
    conn: Any,
    query: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> Any:
    return conn.execute(
        _sql(query),
        params,
    )


def _fetchone(
    conn: Any,
    query: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> Any | None:
    cursor = _execute(
        conn,
        query,
        params,
    )

    return cursor.fetchone()


def _fetchall(
    conn: Any,
    query: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> list[Any]:
    cursor = _execute(
        conn,
        query,
        params,
    )

    return cursor.fetchall()


def _row_to_dict(
    row: Any | None,
) -> dict[str, Any] | None:
    if row is None:
        return None

    return dict(row)


# ============================================================
# SCHEMA / MIGRATIONS
# ============================================================

def _columns(
    conn: Any,
    table: str,
) -> set[str]:
    """
    Hämtar befintliga kolumner för migrationskontroll.
    """

    if USE_POSTGRES:
        rows = _fetchall(
            conn,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table,),
        )

        return {
            row["column_name"]
            for row in rows
        }

    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def _ensure_column(
    conn: Any,
    table: str,
    column: str,
    definition: str,
) -> None:
    """
    Enkel bakåtkompatibel migration för redan skapade databaser.
    """

    if column in _columns(
        conn,
        table,
    ):
        return

    # table, column och definition skickas endast från vår egen kod.
    conn.execute(
        f"""
        ALTER TABLE {table}
        ADD COLUMN {column} {definition}
        """
    )


def init_db() -> None:
    """
    Skapar databasschema automatiskt.

    Fungerar med:
    - PostgreSQL i Render/produktion
    - SQLite lokalt
    """

    schema_statements = [
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
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS revisions (
            id TEXT PRIMARY KEY,

            project_id TEXT NOT NULL,

            revision_number INTEGER NOT NULL,

            instruction TEXT NOT NULL,
            summary TEXT NOT NULL,

            notes_json TEXT NOT NULL DEFAULT '[]',
            files_json TEXT NOT NULL,

            created_at TEXT NOT NULL,

            FOREIGN KEY(project_id)
                REFERENCES projects(id)
                ON DELETE CASCADE,

            UNIQUE(
                project_id,
                revision_number
            )
        )
        """,

        """
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

            FOREIGN KEY(project_id)
                REFERENCES projects(id)
                ON DELETE CASCADE
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_revisions_project
        ON revisions(
            project_id,
            revision_number DESC
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_deployments_project
        ON deployments(
            project_id,
            created_at DESC
        )
        """,
    ]

    with db() as conn:

        for statement in schema_statements:
            conn.execute(statement)

        # ----------------------------------------------------
        # Lightweight migrations för äldre WebbBuilder-databaser
        # ----------------------------------------------------

        _ensure_column(
            conn,
            "projects",
            "github_repo",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "github_repo_id",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "github_repo_url",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "github_branch",
            "TEXT DEFAULT 'main'",
        )

        _ensure_column(
            conn,
            "projects",
            "last_commit_sha",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "deploy_provider",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "deploy_service_id",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "live_url",
            "TEXT",
        )

        _ensure_column(
            conn,
            "projects",
            "auto_publish",
            "INTEGER NOT NULL DEFAULT 0",
        )


# ============================================================
# PROJECTS
# ============================================================

def create_project(
    name: str,
    project_type: str,
    stack: str,
    brief: str,
) -> str:

    project_id = uuid.uuid4().hex[:16]
    now = utcnow()

    with db() as conn:

        _execute(
            conn,
            """
            INSERT INTO projects (
                id,
                name,
                project_type,
                stack,
                brief,
                status,
                created_at,
                updated_at
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                'draft',
                ?,
                ?
            )
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

        rows = _fetchall(
            conn,
            """
            SELECT
                p.*,

                COALESCE(
                    (
                        SELECT MAX(
                            r.revision_number
                        )
                        FROM revisions r
                        WHERE r.project_id = p.id
                    ),
                    0
                ) AS revision_count

            FROM projects p

            ORDER BY p.updated_at DESC
            """,
        )

    return [
        dict(row)
        for row in rows
    ]


def get_project(
    project_id: str,
) -> dict[str, Any] | None:

    with db() as conn:

        row = _fetchone(
            conn,
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (
                project_id,
            ),
        )

    return _row_to_dict(row)


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

    values = (
        list(clean.values())
        + [project_id]
    )

    with db() as conn:

        _execute(
            conn,
            f"""
            UPDATE projects
            SET {columns}
            WHERE id = ?
            """,
            values,
        )


def delete_project(
    project_id: str,
) -> None:

    with db() as conn:

        _execute(
            conn,
            """
            DELETE FROM projects
            WHERE id = ?
            """,
            (
                project_id,
            ),
        )


# ============================================================
# REVISIONS
# ============================================================

def next_revision_number(
    project_id: str,
) -> int:

    with db() as conn:

        row = _fetchone(
            conn,
            """
            SELECT
                COALESCE(
                    MAX(revision_number),
                    0
                ) AS max_rev

            FROM revisions

            WHERE project_id = ?
            """,
            (
                project_id,
            ),
        )

    if not row:
        return 1

    return int(
        row["max_rev"]
    ) + 1


def add_revision(
    project_id: str,
    instruction: str,
    summary: str,
    files: dict[str, str],
    notes: list[str] | None = None,
) -> int:

    revision_number = next_revision_number(
        project_id
    )

    now = utcnow()

    notes_json = json.dumps(
        notes or [],
        ensure_ascii=False,
    )

    files_json = json.dumps(
        files,
        ensure_ascii=False,
    )

    with db() as conn:

        _execute(
            conn,
            """
            INSERT INTO revisions (
                id,
                project_id,
                revision_number,
                instruction,
                summary,
                notes_json,
                files_json,
                created_at
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                uuid.uuid4().hex,
                project_id,
                revision_number,
                instruction.strip(),
                summary.strip(),
                notes_json,
                files_json,
                now,
            ),
        )

        _execute(
            conn,
            """
            UPDATE projects

            SET
                status = 'generated',
                updated_at = ?

            WHERE id = ?
            """,
            (
                now,
                project_id,
            ),
        )

    return revision_number


def list_revisions(
    project_id: str,
) -> list[dict[str, Any]]:

    with db() as conn:

        rows = _fetchall(
            conn,
            """
            SELECT
                id,
                project_id,
                revision_number,
                instruction,
                summary,
                notes_json,
                created_at

            FROM revisions

            WHERE project_id = ?

            ORDER BY revision_number DESC
            """,
            (
                project_id,
            ),
        )

    result: list[dict[str, Any]] = []

    for row in rows:

        item = dict(row)

        raw_notes = item.pop(
            "notes_json",
            "[]",
        )

        try:
            item["notes"] = json.loads(
                raw_notes or "[]"
            )

        except (
            TypeError,
            json.JSONDecodeError,
        ):
            item["notes"] = []

        result.append(item)

    return result


def get_revision(
    project_id: str,
    revision_number: int,
) -> dict[str, Any] | None:

    with db() as conn:

        row = _fetchone(
            conn,
            """
            SELECT *

            FROM revisions

            WHERE project_id = ?
              AND revision_number = ?
            """,
            (
                project_id,
                revision_number,
            ),
        )

    if not row:
        return None

    item = dict(row)

    raw_files = item.pop(
        "files_json",
        "{}",
    )

    raw_notes = item.pop(
        "notes_json",
        "[]",
    )

    try:
        item["files"] = json.loads(
            raw_files or "{}"
        )

    except (
        TypeError,
        json.JSONDecodeError,
    ):
        item["files"] = {}

    try:
        item["notes"] = json.loads(
            raw_notes or "[]"
        )

    except (
        TypeError,
        json.JSONDecodeError,
    ):
        item["notes"] = []

    return item


def latest_revision(
    project_id: str,
) -> dict[str, Any] | None:

    with db() as conn:

        row = _fetchone(
            conn,
            """
            SELECT *

            FROM revisions

            WHERE project_id = ?

            ORDER BY revision_number DESC

            LIMIT 1
            """,
            (
                project_id,
            ),
        )

    if not row:
        return None

    item = dict(row)

    raw_files = item.pop(
        "files_json",
        "{}",
    )

    raw_notes = item.pop(
        "notes_json",
        "[]",
    )

    try:
        item["files"] = json.loads(
            raw_files or "{}"
        )

    except (
        TypeError,
        json.JSONDecodeError,
    ):
        item["files"] = {}

    try:
        item["notes"] = json.loads(
            raw_notes or "[]"
        )

    except (
        TypeError,
        json.JSONDecodeError,
    ):
        item["notes"] = []

    return item


# ============================================================
# DEPLOYMENTS
# ============================================================

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

    deployment_row_id = (
        uuid.uuid4().hex
    )

    with db() as conn:

        _execute(
            conn,
            """
            INSERT INTO deployments (
                id,
                project_id,
                provider,
                status,
                deployment_id,
                service_id,
                url,
                commit_sha,
                message,
                created_at
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
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


def list_deployments(
    project_id: str,
) -> list[dict[str, Any]]:

    with db() as conn:

        rows = _fetchall(
            conn,
            """
            SELECT *

            FROM deployments

            WHERE project_id = ?

            ORDER BY created_at DESC

            LIMIT 30
            """,
            (
                project_id,
            ),
        )

    return [
        dict(row)
        for row in rows
    ]
