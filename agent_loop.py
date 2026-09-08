import os
import sys
import argparse
import uuid
from openai import OpenAI
from dotenv import load_dotenv

from agent_tools import create_agent_tools
from memory_manager import SQLiteMemory, VectorMemory
from compression_agent import compress_session_memory

# Load environment variables
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY not found in .env file.")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Cognitive Database Copilot Agent")
    parser.add_argument("db_uri", help="SQLAlchemy connection string (e.g., sqlite:///company.db)")
    args = parser.parse_args()
    
    print(f"🔧 Initializing Cognitive Agent with Database: {args.db_uri}")
    
    # 0. Initialize Memory Systems & Session ID
    session_id = uuid.uuid4().hex
    sqlite_mem = SQLiteMemory()
    vector_mem = VectorMemory()
    print(f"🧠 Memory Modules Initialized. Session ID: {session_id}")
    
    # 1. Initialize Client
    client = OpenAI(api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'))
    
    # 2. Get the tools bound to this specific database
    tools = create_agent_tools(args.db_uri)
    
    # 3. Create the System Instruction
    system_instruction = """You are a Multi-Agent Database Copilot and Data Scientist.
You have access to tools to fetch the database schema and execute Python code.
Whenever you are asked to analyze data:
1. ALWAYS use the `get_database_schema` tool first to understand the tables.
2. Write Python code using pandas and SQLAlchemy to query the database.
3. ALWAYS use the `execute_python_code` tool to run your code and see the result.
4. If the code fails, read the error message, fix your code, and try again.
5. NEVER write INSERT, UPDATE, DELETE, or DROP statements. Only SELECT.
6. MANDATORY: In your final explanation to the user, you MUST include the EXACT Python code you successfully executed, formatted in a ```python ... ``` markdown block. If you do not include the code, you have failed your task.
7. Explain the final result clearly to the user in English.
"""

    messages = [{"role": "system", "content": system_instruction}]
    tools_schema, tool_map = create_agent_tools(args.db_uri)
    
    print("\n" + "="*50)
    print("🤖 Cognitive Agent is Ready!")
    print("💡 (Type 'exit' or 'quit' to trigger memory compression and stop)")
    print("="*50 + "\n")
    
    # 5. Interactive Loop (Memory Injection & Preservation)
    while True:
        try:
            user_input = input("You: ")
            
            # --- 拦截退出，触发延迟压缩机制 ---
            if user_input.strip().lower() in ['exit', 'quit']:
                print("\n💤 Shutting down. Initiating background memory compression...")
                compress_session_memory(session_id)
                break
            if not user_input.strip():
                continue
                
            # --- VECTOR SEARCH: 注入长期记忆 ---
            # 在发送给大模型前，先去 ChromaDB 捞一下有没有相关的往期压缩逻辑
            recalled_memories = vector_mem.search_memory(user_input, n_results=1)
            
            # 把原生的一问一答存入 SQLite 短期记忆
            sqlite_mem.add_message(session_id, "user", user_input)
            
            # 组合最终发给大模型的 Prompt
            if recalled_memories:
                print(f"📖 [Memory Recall] Found related past semantic knowledge from Vector DB!")
                enriched_input = f"[Recalled Past Knowledge: {recalled_memories[0]}]\n\nCurrent Request: {user_input}"
            else:
                enriched_input = user_input
                
            print("⏳ Agent is thinking and working (calling tools in background)...")
            import json
            messages.append({"role": "user", "content": enriched_input})
            while True:
                response = client.chat.completions.create(
                    model="gemma-4-31b-it",
                    messages=messages,
                    tools=tools_schema,
                    temperature=0.0
                )
                msg = response.choices[0].message
                msg_dict = {"role": msg.role, "content": msg.content}
                if msg.tool_calls:
                    msg_dict["tool_calls"] = [{"id": t.id, "type": t.type, "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in msg.tool_calls]
                messages.append(msg_dict)

                if msg.tool_calls:
                    for t in msg.tool_calls:
                        fname = t.function.name
                        fargs = json.loads(t.function.arguments)
                        if fname in tool_map:
                            res = tool_map[fname](**fargs)
                        else:
                            res = "Tool not found"
                        messages.append({"role": "tool", "tool_call_id": t.id, "name": fname, "content": str(res)})
                else:
                    final_text = msg.content or ""
                    break
            
            sqlite_mem.add_message(session_id, "agent", final_text)
            print(f"\n🤖 Agent: {final_text}\n")
            
        except KeyboardInterrupt:
            # 即使用户按 Ctrl+C 强退，也会触发静默压缩
            print("\n💤 Forced shutdown. Initiating background memory compression...")
            compress_session_memory(session_id)
            break
        except Exception as e:
            print(f"\n❌ Chat Error: {e}\n")

if __name__ == "__main__":
    main()
