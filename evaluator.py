import os
import time
import sys
from google import genai
from dotenv import load_dotenv
from agent_tools import create_agent_tools

# 设定我们要测评的基准数据库
DB_URI = "sqlite:///examples/company.db"

# 黄金数据集 (Golden Dataset)
# 每个用例包含：提问 (question) 和 必须出现在回答中的预期关键词 (expected_keywords)
TEST_CASES = [
    # --- Level 1: Basic Aggregation ---
    {
        "question": "Who is the highest paid employee?",
        "expected_keywords": ["charlie", "110000"]
    },
    {
        "question": "Which department generated the most sales?",
        "expected_keywords": ["sales"]
    },
    # --- Level 2: Trap / Null Handling ---
    {
        "question": "How much total sales did Charlie make?",
        "expected_keywords": ["0", "zero", "none", "didn't make"]
    },
    # --- Level 3: Date Filtering & Conditional ---
    {
        "question": "What is the total sales amount in February 2023?",
        "expected_keywords": ["23000"]
    },
    # --- Level 4: Multi-table Join & Math ---
    {
        "question": "What is the average age of employees who made at least one sale?",
        "expected_keywords": ["30"] # Bob(35), David(25), Eve(30). Avg = 30
    },
    # --- Level 5: Hallucination Resistance (Non-existent data) ---
    {
        "question": "What is the total sales for the HR department?",
        "expected_keywords": ["0", "no", "not exist", "none"]
    },
    # --- Level 6: Ranking & Limits ---
    {
        "question": "Who is the second highest paid employee in the company?",
        "expected_keywords": ["alice", "95000"]
    },
    # --- Level 7: Complex Aggregation & Comparison ---
    {
        "question": "Which employee generated total sales that exceed their own base salary?",
        "expected_keywords": ["bob"] # Bob's salary is 75k, his total sales are 15k+23k+45k = 83k. Only Bob matches.
    }
]

def run_evaluation():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY not found.")
        sys.exit(1)

    print(f"🚀 Starting Agent Automated Evaluation Engine (Golden Dataset Eval)...")
    print(f"📊 Loading test cases (Total {len(TEST_CASES)} questions)")
    print("-" * 50)

    # 初始化大模型 (无记忆隔离模式，确保每次测验是完全公正的 Zero-shot)
    client = genai.Client(api_key=api_key)
    tools = create_agent_tools(DB_URI)
    
    system_instruction = """You are a Multi-Agent Database Copilot and Data Scientist.
You have access to tools to fetch the database schema and execute Python code.
1. ALWAYS use the `get_database_schema` tool first.
2. Write Python code using pandas and SQLAlchemy to query the database.
3. ALWAYS use the `execute_python_code` tool to run your code.
4. Explain the final result clearly.
"""

    total_latency = 0
    total_tokens = 0
    successful_tasks = 0

    results_log = []

    for idx, case in enumerate(TEST_CASES, 1):
        question = case["question"]
        expected = case["expected_keywords"]
        
        print(f"\n▶️ Test Case {idx}: {question}")
        print("⏳ Agent is thinking and executing sandbox code...")
        
        # 每次测试都新建一个干净的 chat session，防止之前的测试污染上下文
        chat = client.chats.create(
            model="gemma-4-31b",
            config={
                "tools": tools,
                "system_instruction": system_instruction,
                "temperature": 0.0
            }
        )
        
        start_time = time.time()
        
        try:
            # 发送请求
            response = chat.send_message(question)
            
            # 计算耗时
            latency = time.time() - start_time
            total_latency += latency
            
            # 获取 Token 消耗 (Google GenAI SDK 结构)
            tokens_used = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tokens_used = response.usage_metadata.total_token_count
            total_tokens += tokens_used
            
            answer_text = response.text.lower()
            
            # 关键词精准命中法 (Keyword Match Eval)
            # 只要预期关键词有【任意一个】命中，我们就认为大模型算对了
            is_success = any(str(kw).lower() in answer_text for kw in expected)
            
            if is_success:
                successful_tasks += 1
                status = "✅ PASS"
            else:
                status = "❌ FAIL"
                
            print(f"[{status}] Latency: {latency:.2f}s | Tokens Used: {tokens_used}")
            if not is_success:
                print(f"   ⚠️ Expected keywords to include: {expected}")
                print(f"   🤖 Actual output excerpt: {response.text[:100]}...")
                
            results_log.append({
                "id": idx,
                "status": status,
                "latency": latency,
                "tokens": tokens_used
            })
            
        except Exception as e:
            print(f"❌ Test crashed during execution: {e}")

    # --- 输出最终评测报告 ---
    print("\n\n" + "="*50)
    print("📈 AGENT Comprehensive Performance Evaluation Report (Metrics Report)")
    print("="*50)
    
    if len(TEST_CASES) == 0:
        return
        
    success_rate = (successful_tasks / len(TEST_CASES)) * 100
    avg_latency = total_latency / len(TEST_CASES)
    avg_tokens = total_tokens / len(TEST_CASES)
    
    # 假设每百万 Token 价格为 0.15 美元 (Gemini Flash Lite 参考价)
    cost_per_million = 0.15
    total_cost_usd = (total_tokens / 1000000) * cost_per_million
    
    print(f"🎯 Task Success Rate : {success_rate:.1f}% ({successful_tasks}/{len(TEST_CASES)})")
    print(f"⚡ Average Latency   : {avg_latency:.2f} s / question")
    print(f"🪙 Avg Token Usage   : {avg_tokens:.0f} Tokens / question")
    print(f"💰 Total Test Cost   : ${total_cost_usd:.5f} USD")
    print("="*50)
    print("💡 Tip: You can include these objective percentage metrics directly in your DS/AI resume!")

if __name__ == "__main__":
    run_evaluation()
