import os
import uuid
import json
import datetime
from sqlalchemy import create_engine, text, Table, Column, Integer, String, MetaData, DateTime
from sqlalchemy.orm import sessionmaker

class DatabaseMemory:
    """
    Episodic memory (short-term)
    SQLite (local) or PostgreSQL (hosted)
    """
    def __init__(self, db_url=None):
        if db_url is None:
            db_url = os.getenv("DATABASE_URL", "sqlite:///data/episodic_memory/memory_episodic.db")
        
        self.db_url = db_url
        if self.db_url.startswith("sqlite"):
            # Ensure local directory exists for sqlite
            db_path = self.db_url.replace("sqlite:///", "")
            if "/" in db_path:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                
        # SQLAlchemy setup
        self.engine = create_engine(self.db_url)
        self.metadata = MetaData()
        
        self.history_table = Table(
            'history', self.metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('session_id', String),
            Column('timestamp', String),
            Column('role', String),
            Column('content', String)
        )
        
        self.sessions_table = Table(
            'sessions', self.metadata,
            Column('session_id', String, primary_key=True),
            Column('title', String),
            Column('created_at', String),
            # Which database the conversation was about. Switching the data source
            # otherwise leaves a session list whose entries no longer mean anything.
            Column('data_source', String),
            # Compression bookkeeping: how many messages have been distilled, and when.
            # Re-running compression on unchanged history would duplicate memory entries
            # and pay for the same summarisation twice.
            Column('compressed_upto', Integer, default=0),
            Column('compressed_at', String),
        )

        self.metadata.create_all(self.engine)
        self._migrate()
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def _migrate(self):
        """Add columns to an existing table. create_all() only creates missing tables.

        The deployed Postgres already has a `sessions` table without `data_source`;
        without this, every insert naming that column fails at runtime.
        """
        from sqlalchemy import inspect

        try:
            existing = {c["name"] for c in inspect(self.engine).get_columns("sessions")}
        except Exception:
            return
        for column, ddl in (
            ("data_source", "ALTER TABLE sessions ADD COLUMN data_source VARCHAR"),
            ("compressed_upto", "ALTER TABLE sessions ADD COLUMN compressed_upto INTEGER DEFAULT 0"),
            ("compressed_at", "ALTER TABLE sessions ADD COLUMN compressed_at VARCHAR"),
        ):
            if column not in existing:
                try:
                    with self.engine.begin() as conn:
                        conn.execute(text(ddl))
                except Exception:
                    pass  # another worker may have added it already

    def add_message(self, session_id: str, role: str, content: str):
        with self.engine.begin() as conn:
            conn.execute(
                self.history_table.insert().values(
                    session_id=session_id,
                    timestamp=datetime.datetime.now().isoformat(),
                    role=role,
                    content=content
                )
            )

    def get_recent_context(self, session_id: str, limit: int = 10) -> list:
        with self.engine.connect() as conn:
            query = self.history_table.select().where(
                self.history_table.c.session_id == session_id
            ).order_by(self.history_table.c.id.desc()).limit(limit)
            
            rows = conn.execute(query).fetchall()
            return [{"role": row.role, "content": row.content} for row in reversed(rows)]

    def get_all_context_for_compression(self, session_id: str) -> str:
        with self.engine.connect() as conn:
            query = self.history_table.select().where(
                self.history_table.c.session_id == session_id
            ).order_by(self.history_table.c.id.asc())
            
            rows = conn.execute(query).fetchall()
            return "\n".join([f"{row.role}: {row.content}" for row in rows])

    def set_session_title(self, session_id: str, title: str):
        # SQLAlchemy upsert varies by dialect, but since we support both, a simple check-and-insert/update is safest
        with self.engine.begin() as conn:
            existing = conn.execute(
                self.sessions_table.select().where(self.sessions_table.c.session_id == session_id)
            ).fetchone()
            
            if existing:
                conn.execute(
                    self.sessions_table.update().where(
                        self.sessions_table.c.session_id == session_id
                    ).values(title=title)
                )
            else:
                conn.execute(
                    self.sessions_table.insert().values(
                        session_id=session_id,
                        title=title,
                        created_at=datetime.datetime.now().isoformat()
                    )
                )

    def get_all_sessions(self, data_source: str | None = None) -> list:
        """Sessions for the sidebar: newest first, with enough to display a row.

        Driven by `sessions` rather than `history` so a session exists even before
        its first message lands, and LEFT JOINed so it still appears if that fails.
        """
        # Rows written before the identity fix hold the prefix-less label
        # ("proj.dataset"), while the browser sends the spec ("marts:proj.dataset").
        # Match both so a conversation does not disappear from its own list.
        params = {}
        if data_source:
            variants = {data_source, data_source.split(":", 1)[-1]}
            where = "WHERE s.data_source IN (" + ", ".join(
                f":ds{i}" for i in range(len(variants))
            ) + ")"
            params = {f"ds{i}": v for i, v in enumerate(sorted(variants))}
        else:
            where = ""
        with self.engine.connect() as conn:
            query = text(f"""
                SELECT s.session_id,
                       s.title,
                       s.created_at,
                       s.data_source,
                       COUNT(h.id)        AS message_count,
                       MAX(h.timestamp)   AS last_at
                FROM sessions s
                LEFT JOIN history h ON h.session_id = s.session_id
                {where}
                GROUP BY s.session_id, s.title, s.created_at, s.data_source
                ORDER BY COALESCE(MAX(h.timestamp), s.created_at) DESC
            """)
            rows = conn.execute(query, params).fetchall()

            return [
                {
                    "id": r[0],
                    "title": r[1] or self._title_from_history(r[0]) or "New conversation",
                    "created_at": r[2],
                    "data_source": r[3],
                    "messages": int(r[4] or 0),
                    "last_at": r[5] or r[2],
                }
                for r in rows
            ]

    def _title_from_history(self, session_id: str, limit: int = 60) -> str:
        """Fall back to the opening question when no title was stored.

        Titles are derived, not generated by a model: a conversation is named after
        the first thing the user asked, which costs nothing and is what a person
        would have written anyway.
        """
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("SELECT content FROM history "
                         "WHERE session_id = :s AND role = 'user' "
                         "ORDER BY id ASC LIMIT 1"),
                    {"s": session_id},
                ).fetchone()
            if not row or not row[0]:
                return ""
            first = " ".join(str(row[0]).split())
            return first[:limit] + ("…" if len(first) > limit else "")
        except Exception:
            return ""

    def touch_session(self, session_id: str, data_source: str | None = None) -> None:
        """Ensure the session row exists, and record which source it is about."""
        with self.engine.begin() as conn:
            row = conn.execute(
                self.sessions_table.select().where(
                    self.sessions_table.c.session_id == session_id
                )
            ).fetchone()
            if row is None:
                conn.execute(self.sessions_table.insert().values(
                    session_id=session_id,
                    title=None,
                    created_at=datetime.datetime.now().isoformat(),
                    data_source=data_source,
                ))
            elif data_source and not row._mapping.get("data_source"):
                conn.execute(
                    self.sessions_table.update()
                    .where(self.sessions_table.c.session_id == session_id)
                    .values(data_source=data_source)
                )

    def compression_state(self, session_id: str) -> dict:
        """How much of this session has already been distilled."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT COALESCE(compressed_upto, 0) AS upto, compressed_at "
                     "FROM sessions WHERE session_id = :s"),
                {"s": session_id},
            ).fetchone()
            count = conn.execute(
                text("SELECT COUNT(*) FROM history WHERE session_id = :s"),
                {"s": session_id},
            ).fetchone()
        return {
            "compressed_upto": int(row[0]) if row else 0,
            "compressed_at": row[1] if row else None,
            "messages": int(count[0]) if count else 0,
        }

    def mark_compressed(self, session_id: str, upto: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                self.sessions_table.update()
                .where(self.sessions_table.c.session_id == session_id)
                .values(compressed_upto=upto,
                        compressed_at=datetime.datetime.now().isoformat())
            )

    def get_messages(self, session_id: str, limit: int = 200) -> list:
        """Full transcript for one session, oldest first, for rehydrating the UI."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT role, content, timestamp FROM history "
                     "WHERE session_id = :s ORDER BY id ASC LIMIT :n"),
                {"s": session_id, "n": limit},
            ).fetchall()
        return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in rows]

    def delete_session(self, session_id: str):
        with self.engine.begin() as conn:
            conn.execute(self.history_table.delete().where(self.history_table.c.session_id == session_id))
            conn.execute(self.sessions_table.delete().where(self.sessions_table.c.session_id == session_id))

# Alias kept for older imports
SQLiteMemory = DatabaseMemory
