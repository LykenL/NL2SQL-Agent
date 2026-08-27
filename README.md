# Cognitive Database Copilot

*A Multi-Agent, LLM-driven Cognitive Database Copilot designed to execute complex data analysis tasks across relational databases. The system seamlessly translates natural language into secure, executable Python/SQL code, evaluates its own execution results, and persists analytical insights across sessions using a custom dual-engine memory architecture.*

## Quick Start

1. **Clone and Install Dependencies**
```bash
git clone https://github.com/LykenL/NL2SQL-Agent.git
cd data_copilot
pip install -r requirements.txt
```

2. **Set up API Key**
```bash
cp .env.example .env
# Open .env and add your GEMINI_API_KEY
```

3. **Run the Copilot (Example DB)**
```bash
python agent_loop.py sqlite:///examples/company.db
```

4. **Run the Automated Benchmarks**
```bash
python evaluator.py
```

---

## Technical Architecture

### 1. Multi-Agent Execution Sandbox (Native Tool Calling)
*   **LLM Engine**: Powered by `Google Gemini 3.5 Flash`, utilizing structured Native Tool Calling.
*   **Agnostic Data Access**: Leveraged `SQLAlchemy` and `Pandas` to support universal connection URIs, allowing the agent to seamlessly query a wide array of SQL dialects (PostgreSQL, MySQL, SQLite) without modifying the codebase.
*   **Secure `exec()` Sandbox**: Developed a robust local execution environment that intercepts `stdout` and injects variables dynamically. Implemented strict AST/Regex-level defense mechanisms to prevent destructive SQL operations (`DROP`, `DELETE`, `UPDATE`), ensuring 100% read-only compliance.
*   **Prompt Injection for Tool Outputs**: Solved the classic "Function Calling Hijack" issue (where LLMs ignore system prompts after receiving successful data) by dynamically injecting "Recency Directives" into the tool's return string, forcing the LLM to output executable code blocks.

### 2. Dual-Engine Cognitive Memory Architecture
Designed a decoupled memory system to handle both short-term interaction states and long-term semantic knowledge retention, significantly reducing context-window bloat and API token costs.

*   **Episodic Memory (Short-Term)**: Utilized **SQLite** to log 100% of raw interactions (user queries, agent responses, and tool execution logs) within a single session.
*   **Semantic Memory (Long-Term)**: Integrated **ChromaDB** to persist high-level logic. 
*   **Asynchronous Compression Agent**: Implemented a secondary background LLM agent triggered upon session termination. It digests the raw SQLite logs, extracts core insights (Entities, Actions, Logic/Results), and embeds them into ChromaDB using `all-MiniLM-L6-v2`.
*   **Contextual KNN Retrieval**: Upon launching a new session, the system automatically performs a k-Nearest Neighbors (KNN) vector search on the user's prompt against ChromaDB, successfully injecting past logical conclusions into the context window (Zero-Shot Cross-Session Recall).

## Empirical Performance & Metrics
Built a custom **Golden Dataset Evaluation Framework** to rigorously benchmark the agent's performance in a zero-shot environment.

| Metric | Result | Description |
| :--- | :--- | :--- |
| **Task Success Rate (TSR)** | **75.0%** (6/8) | Evaluated using Keyword Matching on golden dataset (simple aggregations, complex joins, trap queries, and hallucination tests). |
| **Average Token Usage** | **1,327 Tokens** / query | Highly optimized prompt engineering and memory compression kept token consumption minimal. |
| **Execution Cost** | **<$0.002 USD** / query | Demonstrated extreme cost-efficiency suitable for scalable enterprise deployment. |


