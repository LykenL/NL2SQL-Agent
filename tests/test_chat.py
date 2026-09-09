import os
from dotenv import load_dotenv
from openai import OpenAI
from src.core.agent_tools import create_agent_tools

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = OpenAI(
    api_key=os.getenv('OLLAMA_API_KEY', 'ollama'),
    base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'),
    default_headers={"ngrok-skip-browser-warning": "true"}
)

SYS_INST = "You are a Database Copilot."
tools_schema, tool_map = create_agent_tools("sqlite:///examples/databases/company.db")

print("Creating chat session...")
messages = [{"role": "system", "content": SYS_INST}, {"role": "user", "content": "Who is the highest paid employee?"}]
response = client.chat.completions.create(
    model="gemma4:31b-cloud",
    messages=messages,
    tools=tools_schema,
    temperature=0.0
)

msg = response.choices[0].message
print("Response text:", msg.content)
