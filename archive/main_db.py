import sys
import argparse
from sqlalchemy import create_engine
from db_reader import extract_db_schema
from prompt_builder import build_db_prompt
from llm_client import ask_llm
from executor import extract_code, safe_execute

def main():
    # ==========================================
    # Part 1: read the user's command-line arguments
    # ==========================================
    # Set up the argument parser for the two inputs below
    parser = argparse.ArgumentParser(description="Database-Connected Data Analyst Copilot")
    # arg 1: the database URI (e.g. sqlite:///company.db)
    parser.add_argument("db_uri", help="SQLAlchemy connection string (e.g., sqlite:///company.db)")
    # arg 2: the analytical question in natural language (e.g. "who sold the most")
    parser.add_argument("query", help="Your natural language data analysis request")
    
    # Pull both values out of argv
    args = parser.parse_args()
    
    # ==========================================
    # Part 2: wire the components into a pipeline
    # ==========================================
    
    print(f"\n📊 1. Connecting to Database ({args.db_uri}) & Extracting Schema...")
    # [1] db_reader.py: scan the schema, columns and foreign keys
    schema = extract_db_schema(args.db_uri)
    if schema.startswith("Error"):
        print(schema)
        sys.exit(1)  # schema extraction failed, stop here
        
    print("🧠 2. Building strict DB prompt for LLM...")
    # [2] prompt_builder.py: combine the schema and the question into a strict prompt
    prompt = build_db_prompt(schema, args.query)
    
    print(f"🌐 3. Asking Gemini about: '{args.query}'...")
    # [3] llm_client.py: send the prompt to the model and wait for generated code
    llm_response = ask_llm(prompt)
    
    print("⚙️ 4. Extracting code...")
    # [4] executor.py: strip the prose and keep only the Python code
    code_to_run = extract_code(llm_response)
    
    # Print the extracted code so a human can review it
    print("\n" + "=" * 40)
    print("📝 GENERATED CODE:")
    print("=" * 40)
    print(code_to_run)
    print("=" * 40 + "\n")
    
    # ==========================================
    # Part 3: execute the generated code safely
    # ==========================================
    
    # Package the user's URI (args.db_uri) into a dict of globals.
    # The generated code builds its connection from a variable named `db_uri`.
    # custom_globals injects that real string into the sandbox so the code can run.
    custom_globals = {"db_uri": args.db_uri}
    
    # [5] executor.py: run it through the sandbox
    # Static checks block dangerous calls (os.system, ...) before the code is executed.
    safe_execute(code_to_run, custom_globals=custom_globals)

if __name__ == "__main__":
    main()
