import os
import requests
from dotenv import load_dotenv

load_dotenv()

base_url = os.getenv("OLLAMA_BASE_URL")
api_key = os.getenv("OLLAMA_API_KEY")

url = base_url.replace("/v1", "/api/pull")
headers = {
    "Authorization": f"Bearer {api_key}",
    "ngrok-skip-browser-warning": "true"
}
data = {
    "name": "nomic-embed-text"
}

print(f"Triggering pull on remote server: {url}")
# Stream the response to see progress
with requests.post(url, json=data, headers=headers, stream=True) as r:
    if r.status_code != 200:
        print(f"Failed: {r.status_code} - {r.text}")
    else:
        for line in r.iter_lines():
            if line:
                print(line.decode('utf-8'))
