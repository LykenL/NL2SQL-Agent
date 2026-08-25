import os
from google import genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_gemini_client():
    """
    Initialize and return a Google Gemini client.
    Requires GEMINI_API_KEY environment variable.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found. Please add it to your .env file.")
    
    return genai.Client(api_key=api_key)

def ask_llm(prompt: str, model_name: str = "gemini-3.5-flash") -> str:
    """
    Send a prompt to the LLM and return the text response.
    Default model is gemini-3.5-flash for speed and cost-effectiveness.
    """
    client = get_gemini_client()
    
    print(f"🔄 Requesting Gemini ({model_name})... Please wait.")
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    return response.text

if __name__ == "__main__":
    # Simple test entry point
    test_prompt = "Hello, please give me a one-sentence introduction to Python's pandas library."
    print(f"User: {test_prompt}")
    
    try:
        reply = ask_llm(test_prompt)
        print(f"🤖 Gemini: {reply}")
    except Exception as e:
        print(f"❌ Request failed: {e}")
