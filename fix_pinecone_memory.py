with open('src/core/memory_manager.py', 'r') as f:
    content = f.read()

new_content = content.replace(
    "from openai import OpenAI",
    "from sentence_transformers import SentenceTransformer"
).replace(
    "self.oai = OpenAI()",
    "self.embed_model = SentenceTransformer('all-MiniLM-L6-v2')"
).replace(
    """        response = self.oai.embeddings.create(
            input=text,
            model="text-embedding-3-small" # Requires 1536 dim
        )
        return response.data[0].embedding""",
    "        return self.embed_model.encode(text).tolist()"
)

with open('src/core/memory_manager.py', 'w') as f:
    f.write(new_content)
