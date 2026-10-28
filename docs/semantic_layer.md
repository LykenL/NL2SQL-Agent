# 语义层（R1）：已确认口径注册表 + 执行前校验

> 目标不是"让模型更聪明"，而是**把问题里本来就留着的自由度收掉**，并且**在执行前强制**，而不是靠模型自觉。

## 1. 先看实测：这个库到底有没有歧义

`scripts/probe_definition_variance.py` 对 Chinook 用 5 种措辞、每种 2 次，共 10 次提问"营收是多少"：

| 指标 | 结果 |
|---|---|
| 成功取到数值 | 10 / 10 |
| 不同数值 | **只有 1 个：2,328.60** |
| 数值离散度 | **0.00%** |
| 脚本涉及的列 | 8 次只碰 `Total`；2 次同时出现 `UnitPrice/Quantity`（写注释或校验时引用，未参与计算） |

**结论：在这个库上，答案没有分歧。** 也就是说，若把语义层当作"解决答案不一致"的手段，在这个数据集上是不成立的 —— 模型本身已经稳定选对 `Invoice.Total`。

这决定了 R1 的真实价值只能是下面两条，而不是"防答案漂移"：

1. **强制**：口径由组织确认后，执行前校验，不允许悄悄替换成另一种算法（LLM 换了模型、升级了版本也可能改主意）。
2. **留证**：每条口径有确认人、时间和证据查询，能回答"这个数字按什么定义算的"。

换句话说：**它是治理设施，不是智能增强。** 如果目标场景的 schema 同样干净，这一层的收益接近零，应当把预算投到 R2（成本闸门）。

## 2. 数据模型

`data/semantic/registry.db`（SQLite，已 gitignore）

| 字段 | 说明 |
|---|---|
| `id` | 稳定标识，如 `revenue` |
| `display_name` | 业务名，如"营收" |
| `expr` | **可注入的物理表达式**，如 `SUM(Invoice.Total)` |
| `aliases` | 问法列表，用于词法匹配（中英文都支持） |
| `grain` / `filters` | 粒度与过滤口径，写进 prompt |
| `forbidden_columns` | 明确禁止的替代算法，如 `UnitPrice`、`Quantity` |
| `status` | `draft` → `confirmed` → `stale` |
| `confirmed_by` / `confirmed_at` | 溯源 |
| `evidence_query_id` | 支撑该定义的查询记录 |

状态机：

```
draft ──confirm(by, evidence)──▶ confirmed ──mark_stale()──▶ stale
  ▲                                                            │
  └──────────────── 重新确认 / 修 expr 后重走 ─────────────────┘
```

- `draft`：**不参与强制**，只在 prompt 里作建议。
- `confirmed`：注入 prompt + 执行前校验。
- `stale`：被排除在检索之外（schema 变更后由人工或未来的 schema 监听触发）。

## 3. 检索：不依赖向量库

本地 `chromadb` 因 opentelemetry 版本冲突无法导入，因此检索实现为纯词法，**零依赖、可测、确定性**：

- 拉丁词按词切分；中文按字符 bigram 切分
- 相似度为 Dice 系数；别名短语若字面出现在问题里，直接给 0.95
- 排序：**confirmed 优先**，再按分数
- 默认阈值 0.30，最多返回 3 条

多语言、抗拼写噪声不如 embedding，但换来的好处是：结果可复现，测试里不需要模型。

## 4. 执行前校验（真正的工作量）

`validate_code(code, metrics)` 输出两类结果：

| 级别 | 触发条件 | 处理 |
|---|---|---|
| **block** | 命中了 confirmed 口径，脚本使用了 `forbidden_columns` 里的列，且**没有**同时出现 approved 表达式 | 不执行，把原因回给模型，要求重写 |
| **warn** | 命中了口径，但 approved 表达式完全没出现 | 照常执行，但把提醒附加在 tool 结果末尾 |

关键设计：**当 approved 表达式也存在时，引用禁用列不算违规**。否则"发票口径 vs 明细口径对照"这种合法分析会被误杀。这条有专门测试（`test_comparison_case_is_not_blocked`）。

approved 表达式的识别覆盖三种实际写法：

```python
SUM(Invoice.Total)                          # SQL 字面
df['Total'].sum()                           # 查询后聚合
df.agg(revenue=('Total', 'sum'))            # 命名聚合
```

**已知边界**：校验是正则级的，不做 AST 数据流分析。若脚本把 `UnitPrice*Quantity` 先存成一个变量、再用变量名聚合，校验识别不到——这类情况需要 AST 或运行时列级追踪，属于后续增强，不在 R1 范围内。

## 5. 用法

### CLI

```bash
# 灌入 Chinook 三条定义（幂等）
python scripts/semantic_admin.py seed-chinook

# 确认口径（必须带确认人与证据）
python scripts/semantic_admin.py confirm revenue --by lyken --evidence q_001

# 看某个问题会命中哪条口径
python scripts/semantic_admin.py resolve "今年每个月的营收是多少"

# 新增一条
python scripts/semantic_admin.py add --id aov --name 客单价 \
  --expr "SUM(Invoice.Total)/COUNT(DISTINCT Invoice.InvoiceId)" --alias 客单价 --alias "average order value"

# 拿一个脚本单独校验
python scripts/semantic_admin.py check myscript.py --metric revenue
```

### HTTP

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/metrics?status=` | 列出定义 |
| POST | `/api/metrics` | 新建/更新（draft） |
| POST | `/api/metrics/{id}/confirm?by=&evidence_query_id=` | 确认 |
| POST | `/api/metrics/{id}/stale` | 置为失效 |
| GET | `/api/metrics/resolve?q=` | 看某问题命中哪些口径 |
| POST | `/api/metrics/seed` | 灌入 Chinook 定义 |

### 开关

```bash
SEMANTIC_ENFORCE=off   # 关闭 prompt 注入与校验（用于 A/B 对比）
SEMANTIC_REGISTRY_DB=/path/to/registry.db
```

## 6. 验收证据

| 用例 | 结果 |
|---|---|
| 18 个注册表单测（状态机 / 检索 / prompt 块 / 校验） | 全过 |
| 3 个端到端强制用例（假 LLM 先写错再改对） | 全过：错误脚本没有进入沙盒，只有修正后的执行 |
| 真实模型 + 强制开启（Chinook，"卖唱片卖了多少钱"） | 未误杀，脚本只用 `Total`，回答 2,328.6 |
| 10 次探测（关强制） | 数值 10/10 一致，离散度 0% |

### 尚未实现（明确不在这轮）

- 前端确认 UI（目前走 CLI / HTTP）
- 向量检索（等 chromadb 修好再接）
- AST 级数据流校验
- schema 变更自动置 stale（目前手动）
