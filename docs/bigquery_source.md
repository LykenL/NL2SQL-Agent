# BigQuery 作为数据源（服务账号 + 单源）

## 为什么不是"再换一个连接串"

BigQuery 的凭据不能进 URI：服务账号 JSON 只能存在于服务端。所以它被实现成**数据源对象 + 工具**，而不是一个 `db_uri` 字符串：

| 维度 | SQL 源（sqlite/postgres/mysql） | BigQuery 源 |
|---|---|---|
| 凭据 | 在 URI 里，随请求传递 | 服务账号文件，仅服务端可见 |
| 访问方式 | `DATABASE_URI` + `create_engine` | 只能 `ds.run(sql, max_rows)` |
| 架构信息 | SQLAlchemy inspector（含外键） | Metadata API（无外键，给分区/聚簇/描述） |
| 成本 | 无概念 | 执行前 dry-run，报字节与美元 |

## 配置

服务端环境变量（Render → Environment / Secret Files）：

| 变量 | 作用 |
|---|---|
| `BIGQUERY_DATASETS` | 暴露给 UI 的数据集，逗号分隔：`my-project.analytics,bigquery-public-data.usa_names` |
| `BIGQUERY_BILLING_PROJECT` | 出账项目。读公开数据集时必须设置（数据属主不是你的项目） |
| `BIGQUERY_LOCATION` | 默认 `US` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Secret File 的挂载路径，例如 `/etc/secrets/gcp-sa.json` |
| `BQ_MAX_BYTES` | 单查询字节上限；dry-run 超过即标 `OVER BUDGET` |

本地开发可以省掉服务账号文件，走 `gcloud auth application-default login` 的 ADC。

## 代理可见的工具变化

同一套 tool-calling，工具集按数据源不同：

| 工具 | SQL 源 | BigQuery |
|---|---|---|
| `get_database_schema` | ✅ | ✅（Metadata API，**零扫描字节**） |
| `describe_table` | ✅ | ✅ 单表列/类型/描述/分区键 |
| `run_query(sql, max_rows)` | ✅ | ✅ 只读校验 + 强制 LIMIT + 回报计费字节 |
| `estimate_query(sql)` | — | ✅ 免费 dry-run |
| `execute_python_code` | ✅ 注入 `DATABASE_URI` | ✅ 只注入 `ds`；导入 client / 建连接串会被拒 |

系统提示会按方言注入说明：表名用反引号全限定、`DATE_TRUNC` / `FORMAT_DATE` 而非 `strftime`、并提示"无外键，按命名与列描述推断 JOIN"。

## 安全边界

1. **只读**：`SELECT` / `WITH` 开头才放行；写入/DDL 关键词在字面量剥离后匹配（`deleted_flag` 这类列名不会误杀）。
2. **无连接串**：BigQuery 会话不注入 `DATABASE_URI`，代码里出现 `google.cloud` / `bigquery.Client` / `sqlalchemy_bigquery` / `create_engine` 会被直接拒绝。
3. **行数上限**：所有查询被包成 `SELECT * FROM (...) LIMIT n`，默认 1000 行。
4. **成本闸门**：dry-run 免费，估算值与预算线一起交给模型决定是否改写。

## 实测（真实 BigQuery，2026-10-28）

用公开数据集 `bigquery-public-data.usa_names`、出账项目 `woven-surface-511317-g0`、ADC 凭据：

```
schema（Metadata API）: usa_1910_2013 / usa_1910_current，列含 state/gender/year/name/number 及描述
estimate: 0.09 GB (~$0.0004 @ $5/TiB)
run_query: 返回真实结果（James 4,942,431 …）
只读拦截: DELETE / "SELECT 1; DROP TABLE x" 均被 PermissionError 拦下
```

UI 全链路：连接弹窗 → BigQuery 标签 → 选中 `bigquery-public-data.usa_names` → 表结构进入左侧 DATA CONTEXT → 提问"2013 年最常见的 5 个名字" → 回答 Sophia/Emma/Olivia/Noah/Liam 与人数 → Generated Script 面板给出可重跑的 `ds.run(...)` 片段。

## 已知限制

- **单源**：一个会话一个数据源；不支持同时挂 SQLite + BigQuery。
- **结果体量**：默认最多 1000 行进 pandas；大结果需要先在 SQL 里聚合。
- **缓存计费**：`run_query` 开启查询缓存，命中缓存时回报 `0` 字节，属正常。
- **registry 仍是 SQLite**：Render 重启会丢定义，需挂持久盘或改 Postgres（未做）。
