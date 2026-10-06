import aiosqlite
import os
from contextlib import asynccontextmanager
from server.config import settings

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    tech_stack TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    props_summary TEXT,
    description TEXT,
    category TEXT DEFAULT 'general',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, file_path, name)
);

CREATE TABLE IF NOT EXISTS utilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    signature TEXT,
    description TEXT,
    type TEXT DEFAULT 'util',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, file_path, name)
);

CREATE TABLE IF NOT EXISTS architectural_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    rule_content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    completed_tasks TEXT,
    pending_tasks TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    meeting_date TEXT,
    summary TEXT NOT NULL,
    action_items TEXT,
    raw_notes TEXT,
    source_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'Open',
    description TEXT,
    assignee TEXT,
    url TEXT,
    is_mine INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, external_id)
);


CREATE INDEX IF NOT EXISTS idx_components_project ON components(project_id);
CREATE INDEX IF NOT EXISTS idx_components_name ON components(name);
CREATE INDEX IF NOT EXISTS idx_utilities_project ON utilities(project_id);
CREATE INDEX IF NOT EXISTS idx_rules_project ON architectural_rules(project_id);
CREATE INDEX IF NOT EXISTS idx_session_logs_project ON session_logs(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_meeting_notes_project ON meeting_notes(project_id, meeting_date DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON project_tasks(project_id, status);
"""

@asynccontextmanager
async def get_db_connection():
    db = await aiosqlite.connect(settings.DB_PATH, timeout=15)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA busy_timeout = 15000;")
    try:
        yield db
    finally:
        await db.close()

async def init_db():
    db_dir = os.path.dirname(os.path.abspath(settings.DB_PATH))
    os.makedirs(db_dir, exist_ok=True)

    async with get_db_connection() as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.executescript(CREATE_TABLES_SQL)
        # MigraciÃ³n: eliminar reglas duplicadas (bug previo: cada sync las reinsertaba)
        # y garantizar unicidad por (proyecto, tÃ­tulo).
        await db.execute(
            """
            DELETE FROM architectural_rules
            WHERE id NOT IN (
                SELECT MAX(id) FROM architectural_rules GROUP BY project_id, title
            )
            """
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_unique ON architectural_rules(project_id, title)"
        )
        try:
            await db.execute("ALTER TABLE project_tasks ADD COLUMN is_mine INTEGER DEFAULT 0;")
        except Exception:
            pass
        # Migraciones aditivas: preservar registros anteriores y distinguir
        # repositorios/ramas sin depender del orden de despliegue del cliente.
        additions = {
            "components": {"repo_id": "TEXT NOT NULL DEFAULT 'legacy'", "branch": "TEXT NOT NULL DEFAULT ''",
                           "source_line": "INTEGER", "source_end_line": "INTEGER", "contract": "TEXT",
                           "type_dependencies": "TEXT NOT NULL DEFAULT '[]'", "unresolved_types": "TEXT NOT NULL DEFAULT '[]'",
                           "type_resolution": "TEXT NOT NULL DEFAULT 'not_scanned'", "exported": "INTEGER"},
            "utilities": {"repo_id": "TEXT NOT NULL DEFAULT 'legacy'", "branch": "TEXT NOT NULL DEFAULT ''",
                          "source_line": "INTEGER", "source_end_line": "INTEGER", "contract": "TEXT",
                          "type_dependencies": "TEXT NOT NULL DEFAULT '[]'", "unresolved_types": "TEXT NOT NULL DEFAULT '[]'",
                          "type_resolution": "TEXT NOT NULL DEFAULT 'not_scanned'", "exported": "INTEGER"},
            "session_logs": {"repo_id": "TEXT DEFAULT ''", "branch": "TEXT DEFAULT ''", "commit_sha": "TEXT DEFAULT ''",
                             "ticket_id": "TEXT DEFAULT ''", "agent": "TEXT DEFAULT ''", "evidence": "TEXT", "actor": "TEXT DEFAULT 'legacy'"},
            "meeting_notes": {"source_id": "TEXT", "updated_at": "TEXT"},
            "project_tasks": {"resolved_at": "INTEGER", "source_updated_at": "INTEGER", "source_id": "TEXT",
                              "assignee_ids": "TEXT DEFAULT '[]'", "archived": "INTEGER DEFAULT 0"},
            "architectural_rules": {"author": "TEXT DEFAULT 'legacy'", "revision": "INTEGER DEFAULT 1", "check_spec": "TEXT"},
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # Las tablas antiguas tienen UNIQUE(proyecto,ruta,nombre), que impide
        # conservar el mismo sÃ­mbolo en dos ramas. Reconstruir solo una vez.
        for table in ("components", "utilities"):
            schema = (await (await db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,))).fetchone())["sql"]
            if "UNIQUE(project_id, repo_id, branch, file_path, name)" not in schema:
                replacement = schema.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_scoped").replace(
                    "UNIQUE(project_id, file_path, name)", "UNIQUE(project_id, repo_id, branch, file_path, name)")
                await db.execute(replacement)
                columns = [row["name"] for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()]
                names = ", ".join(columns)
                await db.execute(f"INSERT INTO {table}_scoped ({names}) SELECT {names} FROM {table}")
                await db.execute(f"DROP TABLE {table}")
                await db.execute(f"ALTER TABLE {table}_scoped RENAME TO {table}")
                await db.execute(f"CREATE INDEX idx_{table}_project ON {table}(project_id)")

        await db.executescript("""
        CREATE TABLE IF NOT EXISTS repositories (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            repo_id TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL DEFAULT '',
            dirty INTEGER DEFAULT 0, scanned_at TEXT, synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
            revision INTEGER DEFAULT 1, PRIMARY KEY(project_id, repo_id, branch)
        );
        CREATE TABLE IF NOT EXISTS task_memberships (
            project_id TEXT NOT NULL, external_id TEXT NOT NULL, user_id TEXT NOT NULL, is_mine INTEGER NOT NULL,
            PRIMARY KEY(project_id, external_id, user_id),
            FOREIGN KEY(project_id, external_id) REFERENCES project_tasks(project_id, external_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS rule_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id INTEGER NOT NULL REFERENCES architectural_rules(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, rule_content TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL,
            author TEXT NOT NULL, approved_by TEXT, check_spec TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rule_id, revision)
        );
        CREATE TABLE IF NOT EXISTS webhook_events (
            event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            payload TEXT NOT NULL, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
            last_error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        DROP INDEX IF EXISTS idx_meeting_notes_unique;
        DELETE FROM meeting_notes WHERE source_id IS NULL AND id NOT IN
            (SELECT MAX(id) FROM meeting_notes WHERE source_id IS NULL GROUP BY project_id,title);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_meetings_title_legacy ON meeting_notes(project_id,title) WHERE source_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_meetings_source ON meeting_notes(project_id, source_id) WHERE source_id IS NOT NULL;
        INSERT INTO rule_revisions(rule_id, revision, rule_content, category, status, author, approved_by, check_spec)
        SELECT id, revision, rule_content, category, status, author, CASE WHEN status='active' THEN author END, check_spec
        FROM architectural_rules WHERE id NOT IN (SELECT rule_id FROM rule_revisions);
        """)
        repository_columns = {row["name"] for row in await (await db.execute("PRAGMA table_info(repositories)")).fetchall()}
        if "complete" not in repository_columns:
            await db.execute("ALTER TABLE repositories ADD COLUMN complete INTEGER NOT NULL DEFAULT 0")
        await db.commit()
