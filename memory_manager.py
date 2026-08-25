import sqlite3
import datetime
import chromadb
import uuid
import json

class SQLiteMemory:
    """
    Episodic Memory (短期/近距记忆)
    负责存储明细级别的多轮对话上下文。
    """
    def __init__(self, db_path="memory_episodic.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp DATETIME,
                    role TEXT,
                    content TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at DATETIME
                )
            ''')
            
    def add_message(self, session_id: str, role: str, content: str):
        """保存单条聊天记录"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO history (session_id, timestamp, role, content)
                VALUES (?, ?, ?, ?)
            ''', (session_id, datetime.datetime.now().isoformat(), role, content))
            
    def get_recent_context(self, session_id: str, limit: int = 10) -> list:
        """获取最近 N 条对话上下文"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT role, content FROM history
                WHERE session_id = ?
                ORDER BY id DESC LIMIT ?
            ''', (session_id, limit))
            # Reverse to maintain chronological order
            return [{"role": row[0], "content": row[1]} for row in reversed(cursor.fetchall())]
            
    def get_all_context_for_compression(self, session_id: str) -> str:
        """导出当前 Session 的所有对话，供后台 Agent 压缩使用"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT role, content FROM history
                WHERE session_id = ?
                ORDER BY id ASC
            ''', (session_id,))
            lines = [f"{row[0]}: {row[1]}" for row in cursor.fetchall()]
            return "\n".join(lines)

    def set_session_title(self, session_id: str, title: str):
        """设置或更新会话标题"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO sessions (session_id, title, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET title=excluded.title
            ''', (session_id, title, datetime.datetime.now().isoformat()))

    def get_all_sessions(self) -> list:
        """获取所有历史 Session 列表及标题，按最新对话时间排序"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT h.session_id, MAX(h.timestamp) as last_time, s.title
                FROM history h
                LEFT JOIN sessions s ON h.session_id = s.session_id
                GROUP BY h.session_id
                ORDER BY last_time DESC
            ''')
            
            res = []
            for row in cursor.fetchall():
                sess_id = row[0]
                title = row[2]
                
                if not title:
                    title = "Generating Title..."
                res.append({"id": sess_id, "title": title})
            return res


class VectorMemory:
    """
    Semantic Memory (长期/远距记忆)
    负责利用大模型的 Embeddings 进行高维检索。
    """
    def __init__(self, persist_directory="./chroma_db"):
        # 本地初始化 ChromaDB
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection("semantic_memory")

    def add_memory(self, logic_content: str, metadata: dict = None):
        """存入提取出的 Entity, Action, Result 等高价值逻辑块"""
        if metadata is None:
            metadata = {}
        metadata["timestamp"] = datetime.datetime.now().isoformat()
        
        # ChromaDB 默认使用内置的 all-MiniLM-L6-v2 模型自动进行 Embedding
        self.collection.add(
            documents=[logic_content],
            metadatas=[metadata],
            ids=[str(uuid.uuid4())]
        )

    def search_memory(self, query: str, n_results: int = 3) -> list:
        """根据用户的当前提问，召回相关的历史知识块"""
        # 如果库是空的，直接返回空列表
        if self.collection.count() == 0:
            return []
            
        # 限制召回数量不能大于库中的总条数
        actual_n = min(n_results, self.collection.count())
            
        results = self.collection.query(
            query_texts=[query],
            n_results=actual_n
        )
        
        if not results["documents"] or not results["documents"][0]:
            return []
        
        return results["documents"][0]
