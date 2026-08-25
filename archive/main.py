import sys
import argparse
from data_reader import extract_csv_metadata
from prompt_builder import build_pandas_prompt
from llm_client import ask_llm
from executor import extract_code, safe_execute

def main():
    # Setup command line arguments parser
    parser = argparse.ArgumentParser(description="Text-to-Pandas Data Analysis Copilot")
    parser.add_argument("csv_file", help="Path to the CSV data file")
    parser.add_argument("query", help="Your data analysis request (e.g., 'Calculate the average salary')")
    
    args = parser.parse_args()
    
    # ---------------------------------------------------------
    # STEP 1: Extract Metadata (The "Security Check")
    # ---------------------------------------------------------
    print("\n📊 1. Analyzing CSV metadata...")
    metadata = extract_csv_metadata(args.csv_file)
    if metadata.startswith("Error"):
        print(metadata)
        sys.exit(1)
        
    # ---------------------------------------------------------
    # STEP 2: Prompt Engineering (The "Contract")
    # ---------------------------------------------------------
    print("🧠 2. Building strict prompt for LLM...")
    prompt = build_pandas_prompt(metadata, args.query)
    
    # ---------------------------------------------------------
    # STEP 3: API Call (The "Brain")
    # ---------------------------------------------------------
    print(f"🌐 3. Sending request to Gemini...")
    llm_response = ask_llm(prompt)
    
    # ---------------------------------------------------------
    # STEP 4: Execution (The "Agent")
    # ---------------------------------------------------------
    print("⚙️ 4. Extracting code...")
    code_to_run = extract_code(llm_response)
    
    print("\n" + "=" * 40)
    print("📝 GENERATED CODE:")
    print("=" * 40)
    print(code_to_run)
    print("=" * 40 + "\n")
    
    # Execute safely
    safe_execute(code_to_run)

if __name__ == "__main__":
    main()
