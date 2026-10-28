# Cognitive Database Copilot

*A Multi-Agent, LLM-driven Cognitive Database Copilot designed to execute complex data analysis tasks across relational databases. The system seamlessly translates natural language into secure, executable Python/SQL code, evaluates its own execution results, and persists analytical insights across sessions using a custom dual-engine memory architecture.*

## Stack

| Layer | Technology |
| :--- | :--- |
| Frontend | Next.js + React + Tailwind, dark "fintech dashboard" UI (`frontend/`) |
| API | FastAPI (`src/backend/server.py`) |
| Agent core | OpenAI-compatible native tool calling (`src/core/agent_loop.py`), model via `AGENT_MODEL` (default `nemotron-3-super:cloud`) |
| Execution | `exec()` sandbox with stdout capture (`src/core/executor.py`) |
| Plotting | `plotly.graph_objects` (`fig`) + `altair` (`chart`) |
| Memory | SQLite (episodic) + ChromaDB (semantic) |

> The Streamlit frontend has been removed. The single supported UI is the Next.js app.

## Quick Start

1. **Install backend dependencies**
```bash
pip install -r backend-requirements.txt
```

2. **Configure the model endpoint**
```bash
cp configs/.env.example .env
# OLLAMA_BASE_URL / OLLAMA_API_KEY point at any OpenAI-compatible server
```

3. **Run the API**
```bash
uvicorn src.backend.server:app --reload --port 8000
```

4. **Run the frontend**
```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000 and enter a database URI in the connect dialog.

5. **Enable auto-push (optional, once per clone)**
```bash
./scripts/install-autopush.sh   # git pushes every new commit to its upstream
touch .git/AUTOPUSH_OFF         # disable temporarily
```

6. **Run the automated benchmarks**
```bash
python scripts/evaluator.py
```

## Connecting to Custom Databases

The Copilot uses SQLAlchemy URIs to connect to databases. For SQLite databases, use the `sqlite:///` prefix (four slashes `sqlite:////` for absolute paths on Unix systems).

### 1. Using the Web UI
Enter the connection string in the **Database URI** field of the connect dialog, or upload a `.db` / `.sqlite` / `.csv` file.

### 2. Programmatically
Call the agent loop directly with any SQLAlchemy URI:
```python
from src.core.agent_tools import create_agent_tools
from src.core.agent_loop import run_agent_loop

tools, tool_map = create_agent_tools("sqlite:///path/to/your/database.db")
text, script, _, elapsed = run_agent_loop("Top 5 客户按营收排序", "session-1", tools, tool_map)
```

### Example: Connecting to Chinook Database
```text
sqlite:////Users/lykenl/Documents/local_agent/data_copilot/databases/Chinook_Sqlite.sqlite
```

---

## Technical Architecture

### 1. Multi-Agent Execution Sandbox (Native Tool Calling)
*   **LLM Engine**: OpenAI-compatible native tool calling (Ollama / any OpenAI-compatible endpoint), configured through `OLLAMA_BASE_URL`.
*   **Agnostic Data Access**: `SQLAlchemy` + `Pandas` support universal connection URIs, so the agent queries PostgreSQL, MySQL and SQLite without code changes.
*   **Secure `exec()` Sandbox**: A local execution environment that intercepts `stdout`, captures every DataFrame in scope, and blocks destructive SQL (`DROP`, `DELETE`, `UPDATE`) at the AST/regex level — read-only by construction.
*   **Script capture**: the script that actually ran is taken from the `execute_python_code` tool call and rendered in the **Generated Script** panel. Chat replies are scrubbed of code fences server-side and client-side, so code never appears in the conversation.

### 2. Data Sources (SQLite / Postgres / MySQL / BigQuery)

The agent talks to a `DataSource`, not to a connection string. BigQuery is
authenticated server-side with a service account (Render Secret File), never
exposes credentials to the browser, gets its schema from the metadata API (zero
bytes scanned), and every query is dry-run for bytes/cost first. See
[`docs/bigquery_source.md`](docs/bigquery_source.md).

### 3. Semantic Metric Registry (confirmed definitions, enforced)
Business definitions live in `data/semantic/registry.db` with an expression, a
confirmation trail and an evidence query. Matching definitions are injected into
the prompt, and a generated script that computes an active metric from a
forbidden alternative column is **blocked before execution**. See
[`docs/semantic_layer.md`](docs/semantic_layer.md).

Confirmed definitions show up as badges under each answer, and the
**Definitions** button in the header opens a panel to review, confirm or retire
them and to test which definitions a question would match.

```bash
python scripts/semantic_admin.py seed-chinook
python scripts/semantic_admin.py confirm revenue --by you --evidence q_001
python scripts/semantic_admin.py resolve "今年的营收是多少"
```

Measured on the bundled Chinook DB, 10 runs across 5 phrasings all return the
same number — so the registry's value there is enforcement and provenance, not
answer stability. Run `scripts/probe_definition_variance.py` on your own schema
before assuming otherwise.

### 4. Dual-Engine Cognitive Memory Architecture
*   **Episodic Memory (Short-Term)**: **SQLite** records every user/assistant turn per session.
*   **Semantic Memory (Long-Term)**: **ChromaDB** persists distilled logic.
*   **Asynchronous Compression Agent**: a second background LLM agent digests raw SQLite logs, extracts entities/actions/logic, and embeds them with `all-MiniLM-L6-v2`.
*   **Contextual KNN Retrieval**: new sessions run a KNN vector search over ChromaDB and inject the most relevant past conclusions (zero-shot cross-session recall).

## Empirical Performance & Metrics
Built a custom **Golden Dataset Evaluation Framework** to rigorously benchmark the agent in a zero-shot environment.

| Metric | Result | Description |
| :--- | :--- | :--- |
| **Task Success Rate (TSR)** | **75.0%** (6/8) | Keyword matching on a golden dataset (simple aggregations, complex joins, trap queries, hallucination tests). |
| **Average Token Usage** | **1,327 Tokens** / query | Prompt engineering + memory compression kept token consumption minimal. |
| **Execution Cost** | **<$0.002 USD** / query | Cost-efficient enough for scalable deployment. |
