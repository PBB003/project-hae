from typing import List, Optional, Dict, Any
from server.database import get_db_connection
from server.policy import authorize

class SessionService:
    @staticmethod
    async def add_session_log(
        project_id: str,
        summary: str,
        completed_tasks: Optional[str] = None,
        pending_tasks: Optional[str] = None,
        repo_id: str = "", branch: str = "", commit_sha: str = "", ticket_id: str = "", agent: str = "", evidence: Optional[str] = None,
    ) -> int:
        """
        Registra un resumen de sesión de trabajo en el proyecto.
        Lanza ValueError si el proyecto no existe.
        """
        identity = authorize(project_id, write=True)
        async with get_db_connection() as db:
            async with db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)) as cur:
                if not await cur.fetchone():
                    raise ValueError(f"Proyecto '{project_id}' no existe. Sincronízalo primero con hae.")

            cursor = await db.execute(
                """
                INSERT INTO session_logs (project_id, summary, completed_tasks, pending_tasks,repo_id,branch,commit_sha,ticket_id,agent,evidence,actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, summary.strip(), completed_tasks.strip() if completed_tasks else None, pending_tasks.strip() if pending_tasks else None,
                 repo_id,branch,commit_sha,ticket_id,agent,evidence,identity.user_id),
            )
            await db.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_latest_session_log(project_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene la bitácora de la sesión más reciente del proyecto."""
        authorize(project_id)
        async with get_db_connection() as db:
            async with db.execute(
                """
                SELECT *
                FROM session_logs
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (project_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @staticmethod
    async def get_session_logs(project_id: str, limit: int = 5, ticket_id=None, branch=None) -> List[Dict[str, Any]]:
        """Obtiene el historial de sesiones recientes del proyecto."""
        authorize(project_id)
        sql = "SELECT * FROM session_logs WHERE project_id=?"
        params = [project_id]
        for field, value in [("ticket_id",ticket_id),("branch",branch)]:
            if value is not None:
                sql += f" AND {field}=?"
                params.append(value)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1,min(int(limit),50)))
        async with get_db_connection() as db:
            async with db.execute(
                sql, params,
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
