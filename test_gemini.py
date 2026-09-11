import os
from google import genai
from dotenv import load_dotenv
load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
result = client.models.embed_content(
    model="text-embedding-004",
    contents="Hello world"
)
print("Success, dim:", len(result.embeddings[0].values))
