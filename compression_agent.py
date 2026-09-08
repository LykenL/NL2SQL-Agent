import os
import time
from openai import OpenAI
from dotenv import load_dotenv

from memory_manager import SQLiteMemory, VectorMemory

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

def compress_session_memory(session_id: str):
    """
    Called when a session ends or gets too long.
    It reads the episodic memory (SQLite), uses an LLM to extract logic,
    and saves the embedding into Vector memory (ChromaDB).
    """
    if not api_key:
        print("[Compression Agent] Warning: GEMINI_API_KEY not found.")
        return
        
    client = OpenAI(
        api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), 
        base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'),
        default_headers={"ngrok-skip-browser-warning": "true"}
    )
    
    sqlite_mem = SQLiteMemory()
    vector_mem = VectorMemory()
    
    # 捞出这一次聊天的所有废话和代码执行记录
    raw_chat = sqlite_mem.get_all_context_for_compression(session_id)
    if not raw_chat.strip():
        print("[Compression Agent] No memory to compress for this session.")
        return
        
    print("\n" + "="*50)
    print("🧠 [Compression Agent] Waking up to process episodic memory...")
    
    # 【核心防御】：强制休眠 15 秒，绝对避免主 Agent 刚说完话就触发 5 RPM 红线！
    print("⏳ [Compression Agent] Throttling for 15 seconds to respect rate limits...")
    time.sleep(15)
    
    # 【定制 Prompt】：强制提取你要求的 Entity, Action, Result
    prompt = f"""You are an elite Memory Compression Agent.
Analyze the following raw chat log between a User and a Data Analyst Agent.
Your task is to extract the core insights so they can be saved to a long-term Vector Database.

STRICTLY format your output to include exactly these 3 elements:
1. Entities: (List any people, departments, database tables, or specific data points mentioned)
2. Actions: (What analytical actions or specific SQL queries were performed?)
3. Logic & Results: (What was the final conclusion or key logical step derived from the data?)

Ignore chatty greetings. Focus ONLY on the hard facts.

Raw Chat Log:
{raw_chat}
"""
    
    try:
        response = client.chat.completions.create(
            model="gemma4:31b-cloud",
            messages=[{"role": "user", "content": prompt}],
        )
        compressed_memory = response.choices[0].message.content
        
        print("\n✨ [Compression Agent] Extracted the following Semantic Logic:\n")
        print(compressed_memory)
        print("\n")
        
        # 将提炼出的高级逻辑注入 ChromaDB，自动转换为 Embedding 向量
        vector_mem.add_memory(
            logic_content=compressed_memory,
            metadata={"session_id": session_id}
        )
        print("📥 [Compression Agent] Successfully embedded into Vector Memory!")
        
        # 注意：这里我们故意不删除 SQLite 的源数据，方便你后续对账和 Debug。
        # 实际生产中可以执行 `sqlite_mem.clear_session(session_id)`
        
    except Exception as e:
        print(f"❌ [Compression Agent] Error during compression: {e}")
    finally:
        print("="*50 + "\n")
