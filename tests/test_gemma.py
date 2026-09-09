import os
from google import genai
from google.genai import types
import json

client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

# Try to list models to see exact Gemma 4 names
try:
    for m in client.models.list():
        if 'gemma' in m.name.lower():
            print("Found:", m.name)
except Exception as e:
    print("List error:", e)

# Test gemma-4-31b-it with tools
try:
    def dummy_tool():
        """A tool."""
        pass

    chat = client.chats.create(
        model="gemma-4-31b-it",
        config={
            "tools": [dummy_tool],
            "system_instruction": "You are a test."
        }
    )
    resp = chat.send_message("Hello")
    print("Success:", resp.text)
except Exception as e:
    print("Test error:", e)
