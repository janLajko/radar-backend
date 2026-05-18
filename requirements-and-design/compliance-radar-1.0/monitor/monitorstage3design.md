# Stage 3 — Create Policy Impacts 代码设计

## 1. 概述

Stage 3 读取已通过 Stage 2 过滤的 `radar_policy_updates`，通过 **Claude API Tool Use（agent loop）** 提取受影响的 HTS code 及税率变化，将结构化 JSON 结果写回 `radar_policy_updates.impact_json` 列，并更新 `policy_extract_status`。

**关键设计决策：**
- **不插入 `radar_policy_impacts` 表**，输出为 JSON 存储在 `radar_policy_updates.impact_json`
- 使用 **Claude API Tool Use（多轮工具调用）**，Agent 自主决策何时获取哪些 HTS 数据
- Agent 必须结合**当前最新 HTS 修订版**才能解析政策文件中对 note 的差分修改
- HTS Chapter 99 PDF 按修订版本号**本地缓存**，避免重复下载；Agent 通过 `read_pdf_pages` 工具做 binary search，只读所需页

**触发时机**：每次 worker 周期执行，紧接 Stage 2 之后运行。

---

## 2. 为何需要最新 HTS 数据

政策文件中常出现如下描述：

> U.S. note 16 is modified:
> a. by deleting "headings 9903.85.67" in subdivision (a) and inserting "headings 9903.82.01, 9903.85.67," in lieu thereof
> b. by deleting "Heading 9903.82.06 applies to articles classifiable in the provisions provided for in subdivisions (c)(ii), (iv), (vi) and (vii) of this note." in subdivision (e).

这是**对现有 note 的差分修改**，要推导出实际影响的 HTS code，必须读 note 16 当前的全文：
- subdivision (a) 原来写的是什么 → 删除后变成什么
- subdivision (c)(ii), (iv), (vi), (vii) 对应的商品和国家

**CSV 不够用**：CSV 里只有 `"as provided for in subdivision (c)(i)–(v) of U.S. note 16"`，note 正文只在 PDF 里。

---

## 3. HTS 数据来源

**入口页**：`https://hts.usitc.gov/download/archive`

每个修订版包含：
- `Change Record_XXXX.pdf` — 本次变更记录（Stage 1 已存入 `pdf_urls`）
- Chapter CSV（如 `htsdata.csv`）— 完整税率表，机器可读，用于查询特定 code 税率
- Chapter 99 PDF（如 `Chapter 99_2026HTSRev7.pdf`）— 包含所有 U.S. notes 全文，**note 定位需要此文件**

Agent 在运行时动态获取最新修订版的资源，不依赖 Stage 1 已存的 change record。

---

## 4. 数据库变更

`radar_policy_updates` 新增一列：

```sql
ALTER TABLE radar_policy_updates
ADD COLUMN impact_json jsonb;
```

---

## 5. 处理流程（每条 policy update）

```
new_count = policy_extract_attempt_count + 1

[Step 1] 构建 Agent 初始输入
  └─ source_title, source_content, briefing, source_url, source_key, attachment_text

[Step 2] 运行 Agent Loop（详见第 7 节）
  └─ Agent 自主调用工具：获取 archive index → 下载 CSV → binary search PDF pages
  └─ 返回结构化 impact JSON，或抛出异常

[Step 3] 成功 → 在事务中：
  - UPDATE radar_policy_updates SET impact_json=%s, policy_extract_status='succeeded',
      policy_extract_attempt_count=new_count
  - UPSERT radar_webhook_events: event_type='policy_impact_ready_for_review'

[Step 4] 失败 → 在事务中：
  - UPDATE radar_policy_updates SET policy_extract_status='failed',
      policy_extract_attempt_count=new_count
  - 若 new_count >= 3：UPSERT radar_webhook_events: event_type='attempt_exhausted'
```

---

## 6. Impact JSON 输出格式

```json
{
    "source": {
        "type": "proclamation",
        "id": "11002",
        "url": "https://www.presidency.ucsb.edu/documents/proclamation-11002-...",
        "detected_at": "2026-05-07"
    },
    "hts_modifications": [
        {
            "deleted": ["9903.78.01", "9903.81.87", "9903.85.04"],
            "inserted": ["9903.82.02", "7219.14.00.90"]
        }
    ],
    "measures": [
        {
            "heading": "9903.82.02",
            "note": 16,
            "description": "Articles of aluminum, of steel or of copper...",
            "ad_valorem_rate": 50.0,
            "value_basis": "CIF",
            "country_iso2": null,
            "is_potential": false,
            "effective_start_date": "2026-04-06",
            "excludes_headings": ["9903.82.14"],
            "includes_headings": ["7601", "7604"]
        }
    ]
}
```

- 无法确定时：`"hts_modifications": [], "measures": []`（仍视为成功）

---

## 7. Agent 实现：Claude API Tool Use

### 7.1 工具集

| 工具名 | 输入 | 返回 | 说明 |
|--------|------|------|------|
| `http_get` | `url`, `max_chars` | 响应文本（截断至 max_chars）| 获取 archive 页面、CSV 内容 |
| `read_pdf_pages` | `url`, `pages` | 指定页范围的文本 | **核心工具**：下载 PDF 并提取指定页；按 URL 本地缓存 |
| `search_csv_rows` | `url`, `keyword`, `max_rows` | 匹配行（限制行数）| 从 Chapter CSV 过滤特定 HTS code 行 |

#### `read_pdf_pages` 实现细节

```python
def read_pdf_pages(url: str, pages: str) -> str:
    """
    下载 PDF（按 URL 缓存到本地 /tmp/hts_cache/），
    提取 pages 指定的页范围（如 "150-165"），
    返回文本。
    缓存 key = URL，同一修订版 PDF 只下载一次。
    """
    local_path = _get_cached_pdf(url)   # 若已缓存直接用
    return _extract_pages_text(local_path, pages)
```

### 7.2 Agent Loop 结构

```python
def extract_policy_impact(client: anthropic.Anthropic, input: PolicyImpactInput) -> dict:
    messages = [{"role": "user", "content": _build_prompt(input)}]
    tools = [HTTP_GET_TOOL_DEF, READ_PDF_PAGES_TOOL_DEF, SEARCH_CSV_ROWS_TOOL_DEF]

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return _parse_json_output(response)

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _dispatch_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
```

### 7.3 Agent 执行步骤（System Prompt 设计）

```
Step 1: 解析政策文件
  - 识别 source_key 类型
  - 从正文和附件中提取：
    a. 明确列出的 HTS code
    b. 对 HTS note 的差分修改（"note X is modified by deleting/inserting..."）
    c. 新增的 heading 定义（税率、描述、适用条件）

Step 2: 获取最新 HTS 修订版信息
  - http_get("https://hts.usitc.gov/download/archive")
  - 从页面解析最新修订版的 Chapter 99 PDF URL 和 CSV URL

Step 3: 如涉及 note 差分修改
  Binary Search 定位 note 全文：
  - read_pdf_pages(chapter99_pdf_url, pages="1-5")   → 确认 PDF 结构，找 notes 起始大致位置
  - read_pdf_pages(chapter99_pdf_url, pages="X-Y")   → 二分逼近，直到找到目标 note 编号
  - read_pdf_pages(chapter99_pdf_url, pages="P-Q")   → 读完整 note 文本（通常 5-15 页）
  - 结合差分描述推导：被删除的 heading 集合、新增的 heading 集合

Step 4: 获取受影响 code 的税率
  - search_csv_rows(csv_url, keyword="9903.82", max_rows=50)
  - 从 CSV 行获取 description、General Rate、Special Rate、Column 2 Rate

Step 5: 输出 JSON（包裹在 <json></json> 标签中）

Special case — source_key = hts_archive：
  - 从 change record（已在 attachment_text）中解析变更条目
  - 跳过 Source 为 "PP xxxxx" / "Executive Order" / "Notice" 的条目
  - 只处理其他来源的条目
```

### 7.4 Binary Search 示例（针对 Note 16）

```
调用 1: read_pdf_pages(url, "1-5")
  → 看到 Subchapter I U.S. Notes，Notes 区域在前几页

调用 2: read_pdf_pages(url, "200-210")
  → 看到 note 14、15，note 16 应在后面

调用 3: read_pdf_pages(url, "215-230")
  → 找到 "16." 开头，note 16 从第 218 页开始

调用 4: read_pdf_pages(url, "218-235")
  → 读取 note 16 完整文本（subdivision a, b, c(i)-(x), d, e, f, g...）

总计 4 次调用，每次约 10-15 页文本，context 新增约 15-20KB
```

---

## 8. Context Window 分析

每次 agent run 的 context 构成：

| 来源 | 估算大小 |
|------|---------|
| System prompt | ~2KB |
| 政策文档正文 + briefing | ~8KB |
| 附件 PDF 文本 | ~20KB |
| Binary search 过程（4次调用，每次15页）| ~60KB |
| CSV 查询结果（相关行 50条）| ~10KB |
| **总计** | **~100KB ≈ 75K tokens，远低于 200K 上限** |

PDF 缓存后，同一修订版的后续处理不需要重新下载（只需读不同页范围）。

---

## 9. 文件结构

```
src/radar_backend/
├── worker/stages/
│   └── create_policy_impacts.py          # Stage 主入口（skeleton → 实现）
├── llm/
│   └── policy_impact_extractor.py        # agent loop + 工具实现（新增）
└── db/repositories/
    └── policy_updates.py                 # 新增 fetch_pending_for_policy_impact
                                          # 新增 set_policy_extract_status（含 impact_json）
```

---

## 10. Repository 变更

### `policy_updates.py` 新增方法

**`fetch_pending_for_policy_impact(conn, limit=50) -> list[PolicyUpdate]`**

```sql
SELECT id, source_key, source_label, source_url, source_title,
       source_content, briefing, pdf_urls, source_metadata,
       reference_number, published_at, policy_extract_attempt_count
FROM radar_policy_updates
WHERE policy_extract_status IN ('pending', 'failed')
  AND policy_extract_attempt_count < 3
ORDER BY id ASC
LIMIT %s
```

**`set_policy_extract_status(conn, policy_update_id, status, new_attempt_count, impact_json=None)`**

```sql
UPDATE radar_policy_updates
SET policy_extract_status = %s,
    policy_extract_attempt_count = %s,
    impact_json = COALESCE(%s::jsonb, impact_json),
    updated_at = now()
WHERE id = %s
```

### `webhook_events.py` 新增方法

**`upsert_policy_impact_ready(conn, policy_update_id)`**

```python
# event_type = 'policy_impact_ready_for_review'
# entity_type = 'policy_update'
# channel = 'lark'
# 复用现有 ON CONFLICT DO NOTHING 模式
```

---

## 11. Stage 主入口伪代码

```python
class CreatePolicyImpactsStage:
    name = "create_policy_impacts"

    def run(self, context: WorkerContext) -> StageResult:
        repo = context.repositories
        http = HttpClient()
        client = anthropic.Anthropic(api_key=context.settings.anthropic_api_key)
        extracted = 0

        with context.db.connection() as conn:
            updates = repo.policy_updates.fetch_pending_for_policy_impact(conn)

        for update in updates:
            new_count = update.policy_extract_attempt_count + 1

            # 下载附件（在 agent 外，复用 Stage 2 的 download_and_parse）
            attachment_text = ""
            if update.pdf_urls:
                try:
                    attachment_text = download_and_parse(update.pdf_urls, http)
                except Exception as exc:
                    logger.warning("create_policy_impacts: pdf failed id=%s: %s", update.id, exc)
                    with context.db.transaction() as conn:
                        repo.policy_updates.set_policy_extract_status(conn, update.id, "failed", new_count)
                        if new_count >= 3:
                            repo.webhook_events.upsert_attempt_exhausted(conn, "policy_update", update.id)
                    continue

            # 运行 Agent Loop
            try:
                impact_json = extract_policy_impact(
                    client,
                    PolicyImpactInput(
                        policy_update_id=update.id,
                        source_key=update.source_key,
                        source_title=update.source_title,
                        source_content=update.source_content,
                        briefing=update.briefing,
                        attachment_text=attachment_text,
                        source_url=update.source_url,
                    ),
                )
            except Exception as exc:
                logger.warning("create_policy_impacts: agent failed id=%s: %s", update.id, exc)
                with context.db.transaction() as conn:
                    repo.policy_updates.set_policy_extract_status(conn, update.id, "failed", new_count)
                    if new_count >= 3:
                        repo.webhook_events.upsert_attempt_exhausted(conn, "policy_update", update.id)
                continue

            with context.db.transaction() as conn:
                repo.policy_updates.set_policy_extract_status(
                    conn, update.id, "succeeded", new_count, impact_json=impact_json
                )
                repo.webhook_events.upsert_policy_impact_ready(conn, update.id)
            extracted += 1

        return StageResult(stage_name=self.name, processed_count=extracted)
```

---

## 12. 错误处理策略

| 情况 | 处理 |
|------|------|
| 附件 PDF 下载失败 | `failed`，attempt+1；达 3 次触发 `attempt_exhausted` |
| `http_get` 工具失败（网络）| Agent 工具返回错误文本，Agent 自行决策重试或跳过 |
| `read_pdf_pages` 缓存下载失败 | 工具返回错误，整体 attempt 计为 `failed` |
| Binary search 未找到目标 note | Agent 输出空 impacts，仍视为成功 |
| Agent 输出 JSON 格式非法 | `ValueError` → `failed` |
| `hts_modifications` + `measures` 均为空 | 视为**成功**（文章无直接 HTS 影响）|
| attempt_count 达 3 | 触发 `attempt_exhausted` webhook |

---

## 13. 与 Stage 2 对照

| | Stage 2 | Stage 3 |
|---|---------|---------|
| LLM 调用方式 | 单次 `llm.complete()` | Agent loop（多轮 tool use）|
| 工具 | 无 | `http_get` / `read_pdf_pages` / `search_csv_rows` |
| 外部数据 | 无 | hts.usitc.gov 实时数据（PDF + CSV）|
| PDF 处理 | `download_and_parse`（全文）| `read_pdf_pages`（按页，缓存）|
| 输出目标 | `radar_policy_updates` 各字段 | `radar_policy_updates.impact_json` |
| 核心模块 | `policy_filter.py` | `policy_impact_extractor.py` |
