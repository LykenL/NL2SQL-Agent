import os
import re

# 1. Update agent_loop.py
with open("agent_loop.py", "r") as f:
    loop_code = f.read()

loop_code = loop_code.replace("from google import genai", "from openai import OpenAI")
loop_code = loop_code.replace("client = genai.Client(api_key=api_key)", "client = OpenAI(api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'))")

# Replace chat logic
old_chat = """    # 4. Create the Chat Session (Memory + Function Calling)
    chat = client.chats.create(
        model="gemma-4-31b-it",
        config={
            "tools": tools,
            "system_instruction": system_instruction,
            "temperature": 0.0
        }
    )"""
new_chat = """    messages = [{"role": "system", "content": system_instruction}]
    tools_schema, tool_map = create_agent_tools(args.db_uri)"""
loop_code = loop_code.replace(old_chat, new_chat)

old_send = """            print("⏳ Agent is thinking and working (calling tools in background)...")
            
            # 发送强化过的请求给大模型
            response = chat.send_message(enriched_input)
            
            # 把原生的一问一答存入 SQLite 短期记忆
            sqlite_mem.add_message(session_id, "agent", response.text)
            
            print(f"\\n🤖 Agent: {response.text}\\n")"""
new_send = """            print("⏳ Agent is thinking and working (calling tools in background)...")
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
            print(f"\\n🤖 Agent: {final_text}\\n")"""
loop_code = loop_code.replace(old_send, new_send)
with open("agent_loop.py", "w") as f:
    f.write(loop_code)


# 2. Update compression_agent.py
with open("compression_agent.py", "r") as f:
    comp_code = f.read()

comp_code = comp_code.replace("from google import genai", "from openai import OpenAI")
comp_code = comp_code.replace("client = genai.Client(api_key=api_key)", "client = OpenAI(api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'))")
old_generate = """        response = client.models.generate_content(
            model="gemma-4-31b-it",
            contents=prompt,
        )
        compressed_memory = response.text"""
new_generate = """        response = client.chat.completions.create(
            model="gemma-4-31b-it",
            messages=[{"role": "user", "content": prompt}],
        )
        compressed_memory = response.choices[0].message.content"""
comp_code = comp_code.replace(old_generate, new_generate)
with open("compression_agent.py", "w") as f:
    f.write(comp_code)

print("Migration script 2 executed.")
