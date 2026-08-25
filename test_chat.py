import os
from dotenv import load_dotenv
from google import genai
from agent_tools import create_agent_tools

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

SYS_INST = "You are a Database Copilot."
tools = create_agent_tools("sqlite:///examples/company.db")

print("Creating chat session...")
chat_session = client.chats.create(
    model="gemini-3.5-flash",
    config={"tools": tools, "system_instruction": SYS_INST, "temperature": 0.0}
)

print("Sending message...")
resp = chat_session.send_message("Who is the highest paid employee?")
print("Response text:", resp.text)
