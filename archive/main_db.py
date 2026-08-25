import sys
import argparse
from sqlalchemy import create_engine
from db_reader import extract_db_schema
from prompt_builder import build_db_prompt
from llm_client import ask_llm
from executor import extract_code, safe_execute

def main():
    # ==========================================
    # 第一部分：接收用户在终端输入的命令
    # ==========================================
    # 设置命令行参数解析器，让用户可以在终端输入参数
    parser = argparse.ArgumentParser(description="Database-Connected Data Analyst Copilot")
    # 接收第一个参数：数据库连接字符串 (例如 sqlite:///company.db)
    parser.add_argument("db_uri", help="SQLAlchemy connection string (e.g., sqlite:///company.db)")
    # 接收第二个参数：用户用自然语言提问的分析需求 (例如 "找出销冠")
    parser.add_argument("query", help="Your natural language data analysis request")
    
    # 提取用户在终端输入的这两个参数
    args = parser.parse_args()
    
    # ==========================================
    # 第二部分：串联核心组件 (流水线)
    # ==========================================
    
    print(f"\n📊 1. Connecting to Database ({args.db_uri}) & Extracting Schema...")
    # 【步骤 1】调用 db_reader.py，充当“CT扫描仪”提取数据库的表结构、列名和外键
    schema = extract_db_schema(args.db_uri)
    if schema.startswith("Error"):
        print(schema)
        sys.exit(1) # 如果提取失败，直接停止程序
        
    print("🧠 2. Building strict DB prompt for LLM...")
    # 【步骤 2】调用 prompt_builder.py，把上一步拿到的表结构和用户的提问，拼装成严苛的提示词
    prompt = build_db_prompt(schema, args.query)
    
    print(f"🌐 3. Asking Gemini about: '{args.query}'...")
    # 【步骤 3】调用 llm_client.py，把拼好的提示词发给 Google Gemini，等待它思考并写出代码
    llm_response = ask_llm(prompt)
    
    print("⚙️ 4. Extracting code...")
    # 【步骤 4】调用 executor.py，把大模型回复中无关的废话去掉，只剥离出纯 Python 代码
    code_to_run = extract_code(llm_response)
    
    # 在屏幕上把提取出来的代码打印出来，方便我们监督 AI 写了什么
    print("\n" + "=" * 40)
    print("📝 GENERATED CODE:")
    print("=" * 40)
    print(code_to_run)
    print("=" * 40 + "\n")
    
    # ==========================================
    # 第三部分：安全执行大模型生成的代码
    # ==========================================
    
    # 我们把用户输入的数据库连接字符串（args.db_uri）打包成一个字典。
    # 为什么要这么做？因为大模型在生成的代码里使用了一个叫 `db_uri` 的变量去建连接。
    # 我们通过 custom_globals 把这个真实字符串“偷偷”塞进沙盒里，让代码能顺利跑起来。
    custom_globals = {"db_uri": args.db_uri}
    
    # 【步骤 5】调用 executor.py 里的安全执行器
    # 先做静态检查拦截危险代码 (os.system等)，确认安全后，再动态运行这段生成的 Python 代码
    safe_execute(code_to_run, custom_globals=custom_globals)

if __name__ == "__main__":
    main()
