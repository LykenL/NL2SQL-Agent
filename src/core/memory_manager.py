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
        if "data_source" not in existing:
            try:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN data_source VARCHAR"))
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
        where = "WHERE s.data_source = :ds" if data_source else ""
        params = {"ds": data_source} if data_source else {}
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

class VectorMemory:
    """
    Semantic memory (long-term)
    ChromaDB (local) or Pinecone (hosted)
    """
    def __init__(self, persist_directory="data/chroma_db"):
        self.pinecone_key = os.getenv("PINECONE_API_KEY")
        self.pinecone_host = os.getenv("PINECONE_HOST")
        self.is_pinecone = bool(self.pinecone_key and self.pinecone_host)

        if self.is_pinecone:
            from pinecone import Pinecone
            self.pc = Pinecone(api_key=self.pinecone_key)
            self.index = self.pc.Index(host=self.pinecone_host)
            
            # Use remote Ollama node for embeddings (nomic-embed-text, 768 dim)
            self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            self.ollama_api_key = os.getenv("OLLAMA_API_KEY", "ollama")
        else:
            import chromadb
            os.makedirs(persist_directory, exist_ok=True)
            self.client = chromadb.PersistentClient(path=persist_directory)
            self.collection = self.client.get_or_create_collection("semantic_memory")

    def _get_embedding(self, text: str) -> list:
        # Use remote Ollama /api/embeddings endpoint
        import requests
        url = self.ollama_base_url.replace("/v1", "/api/embeddings")
        headers = {
            "Authorization": f"Bearer {self.ollama_api_key}",
            "ngrok-skip-browser-warning": "true"
        }
        data = {
            "model": "nomic-embed-text",
            "prompt": text
        }
        resp = requests.post(url, json=data, headers=headers)
        if resp.status_code == 200:
            return resp.json()["embedding"]
        else:
            raise Exception(f"Failed to get embedding from Ollama: {resp.text}")

    def add_memory(self, logic_content: str, metadata: dict = None):
        if metadata is None: metadata = {}
        metadata["timestamp"] = datetime.datetime.now().isoformat()
        metadata["text"] = logic_content # Pinecone needs text in metadata to retrieve it
        
        doc_id = str(uuid.uuid4())
        
        if self.is_pinecone:
            vector = self._get_embedding(logic_content)
            self.index.upsert(vectors=[{"id": doc_id, "values": vector, "metadata": metadata}])
        else:
            self.collection.add(
                documents=[logic_content],
                metadatas=[metadata],
                ids=[doc_id]
            )

    def search_memory(self, query: str, n_results: int = 3) -> list:
        if self.is_pinecone:
            vector = self._get_embedding(query)
            results = self.index.query(vector=vector, top_k=n_results, include_metadata=True)
            if not results.matches:
                return []
            # Return a list of texts
            return [match.metadata["text"] for match in results.matches if "text" in match.metadata]
        else:
            if self.collection.count() == 0: return []
            actual_n = min(n_results, self.collection.count())
            results = self.collection.query(query_texts=[query], n_results=actual_n)
            
            if not results["documents"] or not results["documents"][0]:
                return []
            return results["documents"][0]

# Alias kept for older imports
SQLiteMemory = DatabaseMemory
