# Data Copilot

A Next.js interface and FastAPI service for asking questions about connected SQL databases and BigQuery datasets. The agent inspects schemas, queries data, and can return a generated script and chart. A separate Dagster/dbt pipeline ingests Kolada data into BigQuery when configured.

## Data flow

```
Browser (Next.js) ──POST /api/chat──► FastAPI ──► OpenAI-compatible chat model
                                          │            │
                                          │            └── schema/query/Python tools
                                          ├── SQLAlchemy source (SQLite/PostgreSQL/MySQL)
                                          └── BigQuery source (configured datasets or marts)

Generated script ──POST /api/execute──► HTML output ──► chart/output canvas

Kolada v3 ──Dagster──► BigQuery bronze ──dbt staging──► silver views
                                               └──dbt marts──► gold tables
                                       DQ ledger and mart freshness live in meta
```

The optional warehouse pipeline defines three Kolada ingestion assets, dbt staging views (`stg_municipalities`, `stg_kpi_values`, `stg_kpi_catalog`), and the gold tables `mart_municipality_population`, `mart_personnel_cost`, and `mart_kpi_summary`. `mart_freshness` records load time and row count; the `dq_results` Dagster asset writes dbt test results to the metadata dataset. The `dq_gate` asset fails when the newest ledger run contains failures. See the refresh caveat under [Quality and operations](#quality-and-operations).

## Copilot capabilities

| Capability | Current behavior |
|---|---|
| Chat and analysis | The agent can list tables, describe a table, run a SQL query, and execute Python analysis. The backend endpoint `POST /api/chat` returns a complete JSON response; it is not a streaming endpoint. |
| Data connections | The UI accepts `.db`, `.sqlite`, and `.csv` uploads, a SQLAlchemy database URI, or a BigQuery source exposed by the backend. SQLite, PostgreSQL, and MySQL are handled through SQLAlchemy. A single selected source is used for a chat. |
| Schema catalogue | The sidebar shows table columns and foreign keys where available. Agent tools provide a compact table index and on-demand table descriptions. BigQuery schema details come from its metadata API. |
| Charts and scripts | The agent prompt asks for charts for rankings, comparisons, trends, distributions, and shares; scalar lookups do not require one. Plotly and Altair figures are rendered in the canvas, while generated Python is shown separately and can be re-run. Chart generation depends on the model response. |
| Conversations | Session transcripts are stored server-side, listed, reopened, and deleted. The browser keeps the active session ID in local storage. The prompt uses a rolling summary of older turns plus up to the newest six stored messages; the transcript is not deleted by summarization. The message API returns at most 200 messages by default. There is no cross-session vector recall. |
| Metric definitions | A local SQLite registry stores draft, confirmed, and stale definitions. Resolution is lexical; confirmed definitions are inserted into the prompt and checked before the agent's Python tool executes. The UI can inspect, resolve, confirm, and mark definitions stale. |
| BigQuery controls | BigQuery query execution accepts only `SELECT`/`WITH`, rejects write/DDL keywords, and applies a result row limit (default 1,000). The agent also has an estimate tool that performs a dry run and compares the estimate with `BQ_MAX_BYTES`; that estimate is not an unconditional execution gate. |
| Data health and triage | `GET /api/health/data` returns mart freshness and the latest DQ results; the frontend page `/health` renders this data when BigQuery is reachable. `GET /api/dq/triage` exposes read-only DQ triage with evidence queries and heuristic causes. Without usable BigQuery access, the data-health endpoint reports an unconfigured state. |
| Tracing | Langfuse is optional. Both public and secret keys are required, and `LANGFUSE_TRACING=off` disables it. With tracing enabled, chat turns, model calls, and tool calls are recorded. |

**Source boundary.** The `marts:` BigQuery source uses `MartSource`, which rejects explicit references outside `_gold` and `INFORMATION_SCHEMA` and can check table names against the dbt manifest when present. Separately configured `bigquery:` datasets are not subject to that marts-only rule. No claim is made here about cloud IAM permissions; those are outside this repository's code.

**Execution warning.** The backend endpoint `POST /api/execute` runs the submitted Python with `exec()` in the FastAPI process. The service defines no authentication middleware and configures wildcard CORS. Do not expose it to untrusted callers without an external access-control boundary. The BigQuery read-only checks do not turn Python execution or arbitrary SQLAlchemy database URIs into a general-purpose sandbox.

## Stack

Python 3.12 in CI · FastAPI · SQLAlchemy · pandas · Next.js 16 / React 19 / TypeScript · Tailwind CSS 4 · Plotly · Altair · Dagster · dbt-bigquery · pytest · GitHub Actions

The previous Streamlit UI is not present; `frontend/` is the current application.

## Layout

```
src/backend/server.py       FastAPI routes and request handling
src/core/                   agent loop, tools, data sources, execution, sessions,
                            semantic definitions, DQ triage, and Langfuse helpers
src/agents/summarizer.py    rolling summaries for long conversations
frontend/app/               Next.js pages: chat at / and data health at /health
frontend/components/        sessions and metric-definition panels
pipelines/                  Dagster Kolada ingest and DQ assets
dbt/                       staging views, marts, tests, and BigQuery profiles
scripts/                    refresh, semantic administration, and evaluation runners
evals/                      governed-marts cases and BIRD Mini-Dev inputs/results
tests/                      pytest suite
configs/.env.example        example service environment settings
api/index.py                placeholder response; not the FastAPI application
```

## Run locally

Python 3.12 is used by CI. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend-requirements.txt -r pipelines-requirements.txt
cp configs/.env.example .env
# Set a real LLM_API_KEY in .env before sending a chat request.
uvicorn src.backend.server:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open the frontend page at <http://localhost:3000>. On a localhost page, the frontend uses `http://localhost:8000` as its API base. Its non-local API fallback is `https://nl2sql-agent-0hwl.onrender.com`; source code does not verify that hosted service's current availability. `NEXT_PUBLIC_API_URL` can override the API base, subject to the frontend's localhost safeguard.

The backend loads `.env` from the repository root. `configs/.env.example` contains the example settings. The sample SQL source defaults to `sqlite:///examples/databases/company.db`; `DATABASE_URL` overrides that default. The session store defaults to `data/episodic_memory/memory_episodic.db` and also reads `DATABASE_URL` when set. Alternatively, connect a source in the UI.

| Setting | Purpose |
|---|---|
| `LLM_API_KEY` | Required for model calls. `LLM_BASE_URL` and `AGENT_MODEL` select the OpenAI-compatible endpoint and model; the example file points to DeepSeek. |
| `GEMINI_API_KEY` | Optional legacy variable referenced by `archive/`, `tests/manual_*.py`, `scripts/evaluator.py`, and `test_gemini.py`; it is not used by the FastAPI service or current agent. |
| `BQ_PROJECT`, `BQ_PREFIX` | Expose the published `<BQ_PREFIX>_gold` marts in the source picker when both are set. |
| `BIGQUERY_DATASETS` | Comma-separated BigQuery datasets exposed in the source picker, in `project.dataset` form. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional service-account JSON path; local BigQuery clients can also use Application Default Credentials. |
| `BIGQUERY_BILLING_PROJECT`, `BIGQUERY_LOCATION`, `BQ_MAX_BYTES` | BigQuery billing project, location, and estimate warning threshold. `BQ_MAX_BYTES` is used by the estimate tool, not as a universal query cap. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Optional Langfuse configuration. |
| `NEXT_PUBLIC_API_URL` | Optional frontend API-base override. |

## Refresh the warehouse

The refresh script runs Kolada ingestion, `dbt build`, the DQ ledger asset, and then the DQ gate, stopping on the first failed command:

```bash
python scripts/refresh.py
```

It needs the pipeline dependencies, BigQuery credentials/permissions, and the appropriate `BQ_PROJECT` and `BQ_PREFIX`. The script's defaults are defined in `pipelines/` and the dbt profile. It sets a temporary `DAGSTER_HOME` when one is not already set.

**Failure-path detail:** `refresh.py` stops when `dbt build --fail-fast` exits non-zero. In that case it does not proceed to write `dq_results` or materialize `dq_gate`; the gate asset itself blocks when run against a ledger whose latest run contains failures. Do not assume a failed dbt build has already been recorded in the ledger.

## Quality and operations

- Tests: `python -m pytest tests -q` (CI installs `pytest` separately).
- `.github/workflows/data.yml` runs pytest and `dbt parse --target ci`; when `GCP_SA_JSON` is configured, it also runs a CI-target dbt build. The frontend job runs `npm ci` and `npm run build`.
- `.github/workflows/refresh.yml` schedules the warehouse refresh daily at 06:00 UTC and checks freshness rows when the job runs. It requires the `GCP_SA_JSON` secret.
- `.github/workflows/keep-warm.yml` periodically requests `GET /api/health` from the hosted backend on weekdays. This is a workflow definition, not evidence that the external host is currently available.
- The repository has no Dockerfile or Render/Vercel deployment manifest. `api/index.py` is a placeholder handler, not a second FastAPI entry point.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Service status, build identifier, capabilities, tracing state |
| GET | `/api/health/data` | Mart freshness and latest DQ status, or an unconfigured response |
| GET | `/api/datasources` | BigQuery sources configured for the UI |
| GET | `/api/marts` | Mart freshness, DQ counts, and allowed table list |
| GET | `/api/dq/triage` | Diagnosis of the latest DQ run |
| GET | `/api/schema` | Schema for a SQLAlchemy URI or selected BigQuery source |
| POST | `/api/upload` | Upload `.db`, `.sqlite`, or `.csv`; CSV is converted to SQLite |
| POST | `/api/chat` | Run a chat turn; accepts `session_id`, `message`, and optional source fields |
| POST | `/api/execute` | Execute generated Python and return HTML/output plus chart status |
| GET/POST | `/api/metrics` | List or create/update semantic definitions |
| POST | `/api/metrics/{metric_id}/confirm` | Confirm a definition |
| POST | `/api/metrics/{metric_id}/stale` | Mark a definition stale |
| GET | `/api/metrics/resolve` | Lexically resolve definitions for a question |
| POST | `/api/metrics/seed` | Seed bundled demo definitions |
| POST | `/api/sessions` | Create a conversation |
| GET | `/api/sessions` | List conversations, optionally filtered by source |
| GET | `/api/sessions/{session_id}/messages` | Read a conversation transcript |
| DELETE | `/api/sessions/{session_id}` | Delete a conversation and its stored messages |

## Evaluation

`scripts/evaluate_agent.py` evaluates `evals/marts_core.json`. By default it targets `marts:woven-surface-511317-g0.data_copilot_dev_gold`; use `--source` or `EVAL_SOURCE` to select another source. Numeric expectations are resolved from warehouse SQL at run time. `--dry-run` skips model calls but still connects to the source and resolves those expectations.

```bash
python3 scripts/evaluate_agent.py --dry-run --max-cases 3
python3 scripts/evaluate_agent.py --max-cases 3
```

The non-dry run calls the configured LLM and writes a JSON run record under `evals/results/` by default. `scripts/eval_bird.py` is a separate BIRD Mini-Dev SQLite benchmark runner; its benchmark databases are not committed as normal tracked inputs.

## Docs

Supporting notes are in [`docs/operations.md`](docs/operations.md), [`docs/bigquery_source.md`](docs/bigquery_source.md), and [`docs/semantic_layer.md`](docs/semantic_layer.md). Some documentation files describe older architecture or deployment details; use the implementation above and source code for current behavior.
