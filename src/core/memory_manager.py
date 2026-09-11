import os
import uuid
import json
import datetime
from sqlalchemy import create_engine, text, Table, Column, Integer, String, MetaData, DateTime
from sqlalchemy.orm import sessionmaker

class DatabaseMemory:
    """
    Episodic Memory (短期/近距记忆)
    支持 SQLite (本地) 和 PostgreSQL (云端)
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
            Column('created_at', String)
        )
        
        self.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

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

    def get_all_sessions(self) -> list:
        with self.engine.connect() as conn:
            # Group by session_id and get max timestamp
            query = text("""
                SELECT h.session_id, MAX(h.timestamp) as last_time, s.title
                FROM history h
                LEFT JOIN sessions s ON h.session_id = s.session_id
                GROUP BY h.session_id, s.title
                ORDER BY last_time DESC
            """)
            rows = conn.execute(query).fetchall()
            
            res = []
            for row in rows:
                sess_id = row[0]
                title = row[2]
                if not title:
                    title = "Generating Title..."
                res.append({"id": sess_id, "title": title})
            return res

    def delete_session(self, session_id: str):
        with self.engine.begin() as conn:
            conn.execute(self.history_table.delete().where(self.history_table.c.session_id == session_id))
            conn.execute(self.sessions_table.delete().where(self.sessions_table.c.session_id == session_id))

class VectorMemory:
    """
    Semantic Memory (长期/远距记忆)
    支持 ChromaDB (本地) 和 Pinecone (云端)
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

# For backwards compatibility with app.py
SQLiteMemory = DatabaseMemory
