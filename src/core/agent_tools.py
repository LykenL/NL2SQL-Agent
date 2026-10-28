from src.core.datasource import DataSource, SqlDataSource
from src.core.executor import safe_execute


def _schema_preview(schema_json: str, max_chars: int = 12000) -> str:
    """Keep tool results inside the model's budget."""
    if len(schema_json) <= max_chars:
        return schema_json
    return schema_json[:max_chars] + "\n... (truncated — call describe_table for a specific table)"


def create_agent_tools(db_uri: str = None, source: DataSource = None) -> tuple:
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

    return tools_schema, tool_map
