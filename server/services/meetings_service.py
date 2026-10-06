from server.database import get_db_connection
from server.models import MeetingNoteCreate
from server.policy import authorize


class MeetingsService:
    @staticmethod
    async def add_meeting_note(project_id, title, summary, action_items=None, meeting_date=None,
                               raw_notes=None, source_url=None, source_id=None):
        authorize(project_id, write=True)
        payload = MeetingNoteCreate(title=title, summary=summary, action_items=action_items,
            meeting_date=meeting_date, raw_notes=raw_notes, source_url=source_url, source_id=source_id)
        source_id = payload.source_id or payload.source_url or None
        async with get_db_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            if not await (await db.execute("SELECT 1 FROM projects WHERE id=?",(project_id,))).fetchone():
                raise ValueError(f"Proyecto '{project_id}' no existe en HAE.")
            if source_id:
                existing = await (await db.execute("SELECT id FROM meeting_notes WHERE project_id=? AND source_id=?",(project_id,source_id))).fetchone()
            else:
                existing = await (await db.execute("SELECT id FROM meeting_notes WHERE project_id=? AND title=? AND source_id IS NULL",(project_id,payload.title))).fetchone()
            values = (payload.title,payload.summary,payload.meeting_date,payload.action_items,payload.raw_notes,payload.source_url,source_id)
            if existing:
                note_id = existing["id"]
                await db.execute("""UPDATE meeting_notes SET title=?,summary=?,meeting_date=?,action_items=?,raw_notes=?,source_url=?,source_id=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",(*values,note_id))
            else:
                cursor = await db.execute("""INSERT INTO meeting_notes(title,summary,meeting_date,action_items,raw_notes,source_url,source_id,project_id,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",(*values,project_id))
                note_id = cursor.lastrowid
            await db.commit()
            return note_id

    @staticmethod
    async def get_latest_meeting_note(project_id):
        notes = await MeetingsService.list_meeting_notes(project_id,1)
        return notes[0] if notes else None

    @staticmethod
    async def list_meeting_notes(project_id, limit=5):
        authorize(project_id)
        async with get_db_connection() as db:
            rows = await (await db.execute("SELECT * FROM meeting_notes WHERE project_id=? ORDER BY COALESCE(meeting_date,'') DESC,id DESC LIMIT ?",
                (project_id,max(1,min(int(limit),50))))).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    async def get_meeting_detail(project_id, note_id):
        authorize(project_id)
        async with get_db_connection() as db:
            row = await (await db.execute("SELECT * FROM meeting_notes WHERE project_id=? AND id=?",(project_id,note_id))).fetchone()
            return dict(row) if row else None
