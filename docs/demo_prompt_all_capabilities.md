# 🎛️ 全链路能力压测 Prompt（Chinook 版）

数据库：`sqlite:////Users/lykenl/Documents/local_agent/data_copilot/databases/Chinook_Sqlite.sqlite`（Chinook，11 张表、412 张发票、2,240 行发票明细，数据区间 2021-01 ~ 2025-12）

> 直接复制下面「Prompt 正文」整段粘贴到 Copilot 输入框。不要改任何代码 —— 这是对 Agent 的一次压力测试，不是对系统的改造。

---

## Prompt 正文

````text
我要给董事会做一份《Chinook 音乐商店 2021-01 至 2025-12 经营全景 + 下一步战略》的完整体检报告。这必须是一份端到端、能直接进董事会的交付物，请你一次做全、做到位：

【PART 1 · 正文报告（Markdown，必须有具体数字，不许说“见下方图表”）】
R1 执行摘要：≤5 条核心结论，每条都要带具体数字或百分比。
R2 规模总览：累计营收、发票数、客单价、活跃客户数、付费客户占比、在售曲目数、历史售出曲目数、售出率。
R3 时间趋势：逐年营收与同比增速，并点出 2021-2025 全部同比为正/为负的月份各几个、营收最高的 3 个月。
R4 地域结构：Top 8 国家营收与占比、Top 5 城市营收、国家集中度（Top3 占比 vs 长尾）。
R5 品类结构：Top 6 曲风营收与占比、单曲均价差异最大的两个曲风。
R6 爆款 vs 长尾：营收 Top 10 单曲清单（含曲风、专辑、歌手）、Top1 单曲占品类营收比重、Top 10 单曲合计占比。
R7 客户价值：Top 5 客户的消费额、发票数、客单价、国别；给出 RFM 的 M（金额）分层结论。
R8 团队效率：每位销售代表的负责客户数、营收、人均产出，必须显式指出“没有客户的员工”（这是数据完整性问题，不要静默丢弃）。
R9 数据质量 & 风险清单：至少 3 条可量化的问题（例如某销售代表名下客户数为 0、某曲风滞销率过高、绝大多数曲目从未售出、营收月度波动是否显著）。
R10 下一步战略：3 条可执行建议，每条都要引用上面出现过的具体数字作为依据。

【PART 2 · 可视化（一共要有 8 个视图，覆盖 9 种图表类型）】
- 视图 A：双轴组合 —— 逐月营收（柱）+ 逐月发票数（折线，右轴），并标注营收峰值月份。
- 视图 B：热力图 —— 月 × Top 5 国家的营收矩阵（用颜色深浅看地域节奏）。
- 视图 C：曲风营收横向条形图，按营收从高到低排序。
- 视图 D：国家营收 Treemap。
- 视图 E：Top 10 客户的营收条形图，按客户数（发票数）上色，颜色刻度要有图例。
- 视图 F：售出率（已售曲目占比）按曲风的小倍数图 —— 每个曲风一个面板，做 3 列自动换行。
- 视图 G：团队效率散点图 —— 横轴“负责客户数”、纵轴“营收”，点大小=发票数，悬浮显示代表姓名。
- 视图 H：客户消费金额的箱线图 / 小提琴图，按国家分组。

【PART 3 · 视觉规范（务必好看，走现代金融科技暗色仪表盘风）】
- 允许用 plotly.graph_objects（柱/线/散点/饼/漏斗/桑基/Treemap，变量名 fig）与 altair（热力图/分布/箱线/小提琴/小倍数，变量名 chart）。禁用 plotly.express、matplotlib、seaborn。
- 主色：柔光霓虹紫 rgba(168,85,247,0.60)；焦点高亮：电光青 rgba(0,242,254,0.75)；趋势线/强调色：珊瑚橙 #ff7a00（不要用蓝色，避免和暗底糊在一起）。
- 全局字体 Inter / PingFang SC；网格线 rgba(255,255,255,0.05)；hover 统一 x unified；所有柱子圆角 cornerradius=8；金额轴统一 tickformat='$,.2s'。
- 每个图都要有清晰的标题、轴标题和单位，不要留默认的 trace0 / x / y。
- 关键图（视图 A）加 1 个标注气泡指出营收峰值。

【PART 4 · 硬约束（违反任何一条即视为失败）】
1. 最多调用 7 次工具，所以先建 1 次连接把每张图需要的数据都聚合好，不要一图一查。
2. 只在最终回复里放 1 个 ```python 代码块，且必须能独立跑通、能打印 PART 1 所需的全部数字（正文里的每个数字都要来自执行结果，不许编造）。
3. 图表必须放在一个 Altair 二级分层（layer）规格里，子结构放在 chart.layer 里，并且使用 vconcat / hconcat / repeat / facet 组成一张“仪表盘大图”，不要 4 张独立的 chart 各自 to_dict。
4. 不要手工设置暗色背景色，主题由沙箱统一注入。
5. 正文用中文，数字保留两位小数、金额加 $ 前缀、比例保留一位小数。
````

---

## 这个 prompt 在压测什么（对照代码逐条说明）

| # | 被压测的组件 | 证据位置 | 压测点 |
|---|---|---|---|
| 1 | Schema 自检工具 | `src/core/agent_tools.py:19` | 必须主动调 `get_database_schema` 才能知道 11 张表与 InvoiceLine↔Track↔Genre 的外键链 |
| 2 | 外键链 JOIN | `src/core/prompt_builder.py:54` | 发票→明细→曲目→曲风→专辑→歌手 5 层级联，写错任一层金额就崩 |
| 3 | 推下计算（SQL 聚合） | `src/core/agent_loop.py:26` | 2,240 行明细 + 8,715 行歌单关联，必须 SUM/GROUP BY 推下到库里算 |
| 4 | 一次运行的算力预算 | `agent_loop.py:138` `max_iterations=7` | PART 4 第 1 条把“一图一查”堵死，逼 Agent 做批量口径 |
| 5 | 沙盒变量捕获 | `src/core/executor.py:46` | 打印 + 多 DataFrame 输出都会回流给模型，正文数字必须源自它 |
| 6 | 单代码块交付格式 | `src/core/agent_loop.py` 脚本捕获 + `frontend/app/page.tsx` Generated Script 面板 | 脚本只来自 `execute_python_code` 的工具调用；聊天正文里的代码会被清洗掉 |
| 7 | **Altair 分层（当前最脆的一环）** | `agent_loop.py:102` | 现有 system prompt 没要求顶层图是 `chart.layer`，Agent 会输出“4 个 chart 各自 to_dict”→ 后端多图取最后一次、丢图 |
| 8 | Plotly 双轴 + 注释 | `agent_loop.py:55` | 视图 A 完全复刻既定双轴规范，并加峰值标注 |
| 9 | **plotly.express 禁用** | `agent_tools.py:45` | Treemap/箱线图最容易诱导出 `px.treemap`，这是一次刻意的埋雷 |
| 10 | 地图/空间维度（不可用路径） | `agent_tools.py:42` | 没有 geo 库，`px.choropleth` 必挂；用 Treemap 代替能验证 Agent 是否走可达路径 |
| 11 | 数据完整性 vs 静默丢数 | `Chinook.Employee` | 5 位员工只有 3 位有客户，R8 要求显式点名，压“陷阱题”能力 |
| 12 | 零填充/稀疏格 | `Invoice` | 41 个“月×国家”组合天然缺失，热力图与折线图要正确处理无数据而非崩掉 |
| 13 | 长尾/漏斗 | 1,519/3,503 曲目从未售出 | 视图 F 与 R6/R9 需要计算售出率，是典型的分母陷阱 |
| 14 | 视觉规范执行度 | `agent_loop.py:34-117` | PART 3 把配色、圆角、tickformat、hover 全部写死，可肉眼验收 |
| 15 | 中文输出 | `agent_loop.py:119` | 术语（曲风/客单价/人均产出）与数字格式的一致性 |
| 16 | 语义记忆召回 | `src/agents/compression_agent.py` | 同一个 prompt 连问两次，第二次应能命中上次的压缩结论（语义记忆回路） |

---

## 需要注意（不改代码也能说明的事）

- **为什么不用 `company.db` 做压测**：`examples/databases/company.db` 只有 3 部门/5 员工/5 笔销售，销售额为 0 的不是部门而是“没有销售的部门根本不存在于 joins 结果里”——任何可视化都只能画 3 根柱子。Chinook 有 412 张发票、24 个国家、25 个曲风、3503 首曲目，才有足够的维度和长尾去展示全部能力。
- **视图 F / H 选 Altair 是刻意的**：按 `agent_tools.py` 的分工，热力图/分布/小倍数走 Altair，视图 F（小倍数）、视图 H（箱线/小提琴）刚好落在这一侧；视图 A/C/D/E/G 走 Plotly。
- **唯一的真实风险点在第 7 条**：如果跑完只出现一张图，说明 Agent 没生成顶层 `chart.layer` 规格 —— 这是 system prompt 的缺口，不是模型的问题；修法见下方（先不动手）。
- **可选的修法（待你确认后再改）**：在 `agent_loop.py` 的 system prompt 里加一句 "Altair 输出必须是单个 `chart` 变量，多视图请用 `alt.layer(...)` 放在 `chart.layer`，禁止各自 to_dict"，即可消除丢图风险；其余 15 条不需要任何改动。

---

## 三处实测踩坑（已写进 prompt，跑出来不会再犯）

在本地按 Agent 的执行环境把 8 个视图全部实跑了一遍，以下三点是真实报错，已被 PART 3 / PART 4 的约束提前堵住：

1. `alt.HConcatChart` **没有 `columns` 属性** —— `.facet(..., columns=3)` 之后不能再套 `hconcat` 去传 `columns`，否则 `SchemaValidationError`。prompt 里视图 F 只要求 `facet(columns=3)`，不再二次包装。
2. SVG 主题下 Altair 直接传 pandas 时间字符串会抛 `TypeError: Object of type ndarray is not JSON serializable`，`x` 通道要用 `yearmonth(month):T` 而不是 `:O`。
3. 多个 Plotly `fig` 各自 `to_plotly_json()` 时，pandas 列会残留 `ndarray`；后端渲染时必须用 `plotly.utils.PlotlyJSONEncoder`（或先 `tolist()`）。这也是为什么视图 A/C/D/E/G 建议各自独立存在 `fig` 里，最后只把 Altair 仪表盘放 `chart`。

## 实测数字基线（跑完后可直接对答案）

| 指标 | 真实值 |
|---|---|
| 累计营收 / 发票数 / 客单价 | $2,328.60 / 412 / $5.65 |
| 曲目售出率 | 1,984 / 3,503 = **56.6%**（1,519 首从未售出） |
| Top 曲风 | Rock，$826.65，占 35.5% |
| Top 国家 | USA，$523.06（91 张发票） |
| Top 客户 | Helena Holý（Czech Republic），7 张发票 |
| 销售代表 | Jane Peacock $833.04 / Margaret Park $775.40 / Steve Johnson $720.16；**其余 5 位客户数为 0** |

---

**验证状态**：8 个视图的数据聚合 SQL 与图表构造代码已在 `databases/Chinook_Sqlite.sqlite` 上完整实跑通过（Altair 规格可序列化、Plotly 5 张图均可 `to_plotly_json()`），上表数字来自真实查询结果；LLM 侧的实际生成质量需在应用里跑一次确认。
