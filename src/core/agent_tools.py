import os
import re

from src.core.datasource import DataSource, SqlDataSource
from src.core.executor import safe_execute


# SELECT col[, col...] FROM table — no WHERE, no aggregate, no arithmetic.
_LOOKS_LIKE_INSPECTION = re.compile(
    r"^\s*select\s+(\*|[\w.\"]+(\s*,\s*[\w.\"]+)*)\s+from\s+[\w.\"]+\s*;?\s*$",
    re.IGNORECASE,
)


def _schema_preview(schema_json: str, max_chars: int = 12000) -> str:
    """Keep tool results inside the model's budget."""
    if len(schema_json) <= max_chars:
        return schema_json
    return schema_json[:max_chars] + "\n... (truncated — call describe_table for a specific table)"


def _bird_shape_feedback(question: str, sql: str, columns: list[str], row_count: int) -> str:
    """Cheap, conservative feedback before a benchmark submission.

    This is intentionally advisory rather than a SQL correctness oracle. It
    catches the common exact-match failures (a row dump for a count, or extra
    columns for a scalar) without rejecting valid grouped questions.
    """
    q = question.lower()
    normal_sql = re.sub(r"\s+", " ", sql.lower())
    grouped = any(token in q for token in ("for each", "by each", "per ", "group by"))
    scalar_request = not grouped and any(token in q for token in (
        "how many", "number of", "how much", "what is the ratio", "what is the highest",
        "what is the lowest", "average ", "total ",
    ))
    aggregate = bool(re.search(r"\b(count|sum|avg|min|max)\s*\(|/", normal_sql))
    warnings: list[str] = []
    if scalar_request and not aggregate:
        warnings.append("question appears to request one aggregate value, but SQL has no aggregate")
    if scalar_request and len(columns) != 1:
        warnings.append(f"question appears scalar, but query returns {len(columns)} columns")
    if scalar_request and row_count != 1:
        warnings.append(
            f"question appears scalar, but query returns {row_count} rows — "
            "add ORDER BY ... LIMIT 1 if a single best/first/highest answer was asked for, "
            "or check whether a join duplicated rows"
        )
    # Ranking questions: "the Nth", "the highest", "the most". A multi-row result is
    # the usual failure here (5 rows for a 1-row gold answer), and the fix is almost
    # always LIMIT or a tighter WHERE — say so instead of only reporting the count.
    limit_missing = (
        row_count > 1
        and re.search(r"\b(the|a) (highest|lowest|largest|smallest|most|fewest|first|last|"
                      r"youngest|oldest|earliest|latest)\b|\btop \d+\b|\bthe \w+-?(highest|largest|most)\b",
                      q)
        and not re.search(r"\blimit\b", normal_sql)
    )
    if limit_missing:
        warnings.append(
            f"question asks for a single best/first item but the query has no LIMIT "
            f"(returns {row_count} rows); add ORDER BY the ranking column DESC/ASC LIMIT 1"
        )
    # A join that duplicates keys inflates counts. Flag the pattern, not the answer.
    if row_count > 1 and re.search(r"\bcount\s*\(\s*\*\s*\)", normal_sql) and " join " in normal_sql:
        warnings.append(
            "COUNT(*) over a join counts duplicated rows; use COUNT(DISTINCT key) if the "
            "question asks how many distinct things there are"
        )
    if _LOOKS_LIKE_INSPECTION.match(sql or ""):
        warnings.append("bare table inspection is not an answer")
    return ("WARN: " + "; ".join(warnings)) if warnings else (
        f"PASS: observed {row_count} rows and {len(columns)} columns; now submit this exact SQL."
    )


def create_agent_tools(db_uri: str = None, source: DataSource = None,
                       benchmark_question: str = "") -> tuple:
    """
    Build the agent's tool set for one data source.

    `db_uri` is kept for backwards compatibility; prefer passing a DataSource.
    """
    ds = source or (SqlDataSource(db_uri) if db_uri else None)
    if ds is None:
        raise ValueError("create_agent_tools needs either db_uri or source")

    def get_database_schema(force_refresh: bool = False) -> str:
        if force_refresh and hasattr(ds, "_schema"):
            ds._schema = None  # type: ignore[attr-defined]
        try:
            return _schema_preview(ds.schema_json())
        except Exception as exc:
            return f"Error extracting schema: {exc}"

    def describe_table(table: str) -> str:
        try:
            return ds.describe(table)
        except Exception as exc:
            return f"Error describing {table}: {type(exc).__name__}: {exc}"

    def run_query(sql: str, max_rows: int = 1000) -> str:
        """Read-only query execution with a row cap; BigQuery reports bytes billed."""
        try:
            df = ds.run(sql, max_rows=max_rows)
        except PermissionError as exc:
            return f"🚫 {exc}"
        except Exception as exc:
            return f"❌ Query failed: {type(exc).__name__}: {exc}"
        head = df.head(50).to_string(index=False)
        extra = ""
        if "bytes_processed" in df.attrs:
            extra = f"\n[billed: {df.attrs['bytes_processed']:,} bytes ≈ ${df.attrs['bytes_billed_usd']}]"
        return f"rows={len(df)}{extra}\n{head}"

    def execute_python_code(code_string: str) -> str:
        print("\n[Tool execution] execute_python_code called.")
        if not isinstance(ds, SqlDataSource) and any(
            token in code_string
            for token in ("google.cloud", "bigquery.Client", "sqlalchemy_bigquery", "create_engine")
        ):
            return (
                "🚫 Blocked: this session queries BigQuery only through `ds.run(sql, max_rows)`. "
                "Do not import a client or build a connection string."
            )
        custom_globals = {"ds": ds}
        # SQL sources keep the familiar DATABASE_URI; BigQuery never hands out a
        # connection string — the agent must go through ds.run().
        if isinstance(ds, SqlDataSource):
            custom_globals["db_uri"] = ds.uri
            custom_globals["DATABASE_URI"] = ds.uri
        output = safe_execute(code_string, custom_globals=custom_globals)
        if isinstance(ds, SqlDataSource):
            return output
        return output + (
            "\n\n[SYSTEM] BigQuery session: query only through ds.run(sql) or run_query; "
            "never construct a client or a connection string."
        )

    ds_hint = (
        "`DATABASE_URI` is also available. "
        if isinstance(ds, SqlDataSource)
        else "Query only via ds.run(sql, max_rows) — there is no connection string. "
    )

    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": "get_database_schema",
                "description": "Retrieves the complete JSON schema of the target data source. Call this tool FIRST whenever you are asked to analyze data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "force_refresh": {
                            "type": "boolean",
                            "description": "Set to True to bypass the schema cache and re-scan the database."
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "describe_table",
                "description": "Returns the columns (name, type, description, partition/cluster keys) of ONE table. Cheaper than dumping the whole schema.",
                "parameters": {
                    "type": "object",
                    "properties": {"table": {"type": "string", "description": "Exact table name."}},
                    "required": ["table"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "run_query",
                "description": (
                    "Executes a read-only SELECT and returns a capped preview. Prefer this over "
                    "execute_python_code for simple aggregations; BigQuery results include bytes billed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "A single SELECT or WITH statement."},
                        "max_rows": {"type": "integer", "description": "Row cap for the preview (default 1000)."}
                    },
                    "required": ["sql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_python_code",
                "description": (
                    "Executes Python code locally and returns stdout or error tracebacks. "
                    f"A `ds` object is injected (dialect: {ds.dialect}); use ds.run(sql, max_rows) to fetch data. "
                    + ds_hint
                    + "VISUALIZATION RULES: "
                    "(1) For bar/line/scatter/pie/funnel charts: use `plotly.graph_objects` and store the figure in a variable named `fig`. "
                    "(2) For heatmaps, distributions, correlation matrices, boxplots, violin plots, faceted layouts: use `altair` and store the chart in a variable named `chart`. "
                    "Do NOT use plotly.express, matplotlib, or seaborn. "
                    "Do NOT manually set dark themes — the sandbox auto-applies a unified dark theme."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_string": {
                            "type": "string",
                            "description": "The raw executable Python code. ALWAYS output clean, complete Python code. Do not include markdown code block formatting."
                        }
                    },
                    "required": ["code_string"]
                }
            }
        },
    ]

    tool_map = {
        "get_database_schema": get_database_schema,
        "describe_table": describe_table,
        "run_query": run_query,
        "execute_python_code": execute_python_code,
    }

    # Marts sessions only: read the DQ ledger and explain what broke.
    if getattr(ds, "meta_dataset", None) and getattr(ds, "allowed", None) is not None:
        def diagnose_data_quality() -> str:
            """Read the latest dbt run from the DQ ledger and grade every failure."""
            import json as _json
            from typing import Any

            from google.cloud import bigquery

            from src.core.dq_triage import DQTriage

            mart: Any = ds  # only MartSource reaches this branch (guarded above)
            try:
                triage = DQTriage(
                    client=bigquery.Client(project=mart.project),
                    project=mart.project,
                    meta_dataset=mart.meta_dataset,
                    gold_dataset=mart.dataset,
                )
                report = triage.report()
            except Exception as exc:
                return f"❌ Triage failed: {type(exc).__name__}: {exc}"
            text = _json.dumps(report, ensure_ascii=False, indent=1, default=str)
            if len(text) > 7000:
                text = text[:7000] + "\n... (truncated)"
            return text

        tools_schema.append({
            "type": "function",
            "function": {
                "name": "diagnose_data_quality",
                "description": (
                    "Reads the latest dbt run from meta.dq_results and, for each failing test, "
                    "runs one cheap evidence query on the guarded gold table (null count, "
                    "duplicate keys, orphan keys, out-of-range values) and returns a heuristic "
                    "cause plus a suggested fix. Read-only; use it whenever the user asks "
                    "'did the data fail', 'which test broke', or before trusting a mart."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        })
        tool_map["diagnose_data_quality"] = diagnose_data_quality

    # BigQuery only: a free dry-run before spending money.
    if ds.supports_estimates:
        def estimate_query(sql: str) -> str:
            try:
                est = ds.estimate(sql)
            except Exception as exc:
                return f"❌ Estimate failed: {type(exc).__name__}: {exc}"
            if est is None:
                return "Estimates are not available for this source."
            note = "  ⚠️ OVER BUDGET — rewrite the query before running." if est.over_budget else ""
            return f"Estimated scan: {est.human()}{note}"

        tools_schema.insert(2, {
            "type": "function",
            "function": {
                "name": "estimate_query",
                "description": (
                    "Free dry-run: returns bytes scanned and estimated cost WITHOUT running the query. "
                    "Call it before any query that might scan a large table, then rewrite (partition "
                    "filters, fewer columns) if the estimate is high."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"sql": {"type": "string", "description": "The SELECT you intend to run."}},
                    "required": ["sql"]
                }
            }
        })
        tool_map["estimate_query"] = estimate_query

    # Benchmark mode only: a single, explicit submission is the answer. Runs are
    # exploratory, so "the last query that executed" is often a sanity check; a
    # dedicated tool makes the agent state its final query deliberately.
    if os.getenv("AGENT_PROMPT", "").lower() == "bird":
        validate_shape = os.getenv("BIRD_VERIFY_SHAPE", "off").lower() in {"1", "on", "true", "yes"}
        value_grounding = os.getenv("BIRD_VALUE_GROUNDING", "off").lower() in {"1", "on", "true", "yes"}

        if validate_shape:
            def validate_query_shape(sql: str) -> str:
                """Execute a proposed answer and report its observed result shape."""
                try:
                    df = ds.run(sql, max_rows=1001)
                except Exception as exc:
                    return f"FAIL: query cannot be submitted: {type(exc).__name__}: {exc}"
                setattr(ds, "_bird_validated_sql", sql)
                return _bird_shape_feedback(benchmark_question, sql, list(df.columns), len(df))

            tools_schema.append({
                "type": "function",
                "function": {
                    "name": "validate_query_shape",
                    "description": "Required before submit_query in BIRD mode. Executes the proposed final SQL and checks rows/columns against the question's likely answer shape.",
                    "parameters": {"type": "object", "properties": {
                        "sql": {"type": "string", "description": "The exact final SQL to validate."}
                    }, "required": ["sql"]},
                },
            })
            tool_map["validate_query_shape"] = validate_query_shape

        if value_grounding:
            def sample_column_values(table: str, column: str, limit: int = 20) -> str:
                """Return distinct non-null values for a known SQLite table column."""
                try:
                    schema = __import__("json").loads(ds.schema_json())
                    entry = schema.get(table)
                    names = {c.get("name") for c in entry.get("columns", [])} if entry else set()
                    if not entry or column not in names:
                        return f"FAIL: {table}.{column} is not in the schema"
                    quoted_table = '"' + table.replace('"', '""') + '"'
                    quoted_column = '"' + column.replace('"', '""') + '"'
                    df = ds.run(
                        f"SELECT DISTINCT {quoted_column} FROM {quoted_table} "
                        f"WHERE {quoted_column} IS NOT NULL LIMIT {max(1, min(int(limit), 50))}",
                        max_rows=50,
                    )
                    return f"values for {table}.{column}:\n" + df.to_string(index=False)
                except Exception as exc:
                    return f"FAIL: could not sample values: {type(exc).__name__}: {exc}"

            tools_schema.append({
                "type": "function",
                "function": {
                    "name": "sample_column_values",
                    "description": "Use before a literal WHERE filter when spelling, category labels, or date formats are uncertain. Returns distinct values from a schema-validated SQLite column.",
                    "parameters": {"type": "object", "properties": {
                        "table": {"type": "string"}, "column": {"type": "string"},
                        "limit": {"type": "integer", "description": "Maximum values (1-50; default 20)."},
                    }, "required": ["table", "column"]},
                },
            })
            tool_map["sample_column_values"] = sample_column_values

        def submit_query(sql: str) -> str:
            """Record the final query. The harness executes exactly this one."""
            # An inspection query (SELECT a, b FROM t) is never the answer to a
            # benchmark question, but it is what an agent submits when it runs out
            # of turns. Rejecting it costs one turn and is far cheaper than scoring
            # a wholesale dump of the table.
            if _LOOKS_LIKE_INSPECTION.match(sql or ""):
                return ("rejected: that is an inspection query (a bare column list with no "
                        "filter or aggregate). Submit the query that answers the question.")
            if validate_shape and getattr(ds, "_bird_validated_sql", None) != sql:
                return "rejected: call validate_query_shape on this exact final SQL before submitting it."
            setattr(ds, "submitted_sql", sql)
            return ("submitted — this query is what will be scored. "
                    "Do not run further queries unless this one failed.")

        tools_schema.append({
            "type": "function",
            "function": {
                "name": "submit_query",
                "description": (
                    "Submit the ONE final query that answers the question. Its result set is the "
                    "answer, so it must return exactly the columns and rows the question asks for — "
                    "no extra context columns, no extra rows. Call it once, when you are done."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"sql": {"type": "string", "description": "The final SQL."}},
                    "required": ["sql"],
                },
            },
        })
        tool_map["submit_query"] = submit_query


    return tools_schema, tool_map
