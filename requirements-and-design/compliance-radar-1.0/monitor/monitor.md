## 数据源
### 监听 [proclamation](https://www.presidency.ucsb.edu/advanced-search?field-keywords=&field-keywords2=&field-keywords3=&from%5Bdate%5D=&to%5Bdate%5D=&person2=&category2%5B%5D=59&items_per_page=25)、[notice](https://www.federalregister.gov/documents/search?conditions%5Bsearch_type_id%5D=3&conditions%5Btype%5D%5B%5D=NOTICE&per_page=40)、[Executive Order](https://www.presidency.ucsb.edu/advanced-search?field-keywords=&field-keywords2=&field-keywords3=&from%5Bdate%5D=&to%5Bdate%5D=&person2=&category2%5B0%5D=58&items_per_page=25&page=433)、[hts.usitc最新的archive](https://hts.usitc.gov/download/archive)

#### [proclamation](https://www.presidency.ucsb.edu/advanced-search?field-keywords=&field-keywords2=&field-keywords3=&from%5Bdate%5D=&to%5Bdate%5D=&person2=&category2%5B%5D=59&items_per_page=25) monitor

example: 阅读 https://www.presidency.ucsb.edu/documents/proclamation-11002-adjusting-imports-semiconductors-semiconductor-manufacturing-equipment
下载附件：https://www.presidency.ucsb.edu/sites/default/files/documents_with_attached_files/385907/177899.pdf

根据文章内容 & 附件信息，提取本次收到影响的hts code


#### [Executive Order](https://www.presidency.ucsb.edu/advanced-search?field-keywords=&field-keywords2=&field-keywords3=&from%5Bdate%5D=&to%5Bdate%5D=&person2=&category2%5B0%5D=58&items_per_page=25&page=433) monitor
example: 阅读 https://www.presidency.ucsb.edu/documents/executive-order-14388-continuing-the-suspension-duty-free-de-minimis-treatment-for-all

下载附件：https://www.presidency.ucsb.edu/sites/default/files/documents_with_attached_files/386411/184581.pdf

根据文章内容 & 附件信息，提取本次收到影响的hts code

#### [notice](https://www.federalregister.gov/documents/search?conditions%5Bsearch_type_id%5D=3&conditions%5Btype%5D%5B%5D=NOTICE&per_page=40) monitor

example: 阅读 https://www.federalregister.gov/documents/2026/04/23/2026-07987/procedures-for-submissions-by-certain-steel-and-aluminum-producers-committing-to-new-us-steel-or

根据文章内容，提取本次收到影响的hts code

fr notice有很多，这里可以先筛选一下notice是不是在这Industry and Security Bureau，International Trade Administration，Trade Representative, Office of United States ， Office of United States,U.S. Customs and Border Protection其中，然后再用LLM筛选

#### [hts.usitc](https://hts.usitc.gov/download/archive)
当检测到新的archive，获取archive,并下载change record，例子：Change Record_2026HTSBasic (1).pdf
需要去读这个change record，change record中的内容很有可能已经通过proclamation、notice、Executive Order提前检测到，所以需要根据Source字段过滤：直接跳过 Source 为 `PP xxxxx`（Proclamation）、`Executive Order`、`Notice` 的条目，只处理其他类型的 Source，通过 archive 章节来提取本次受到影响的 hts code。

### 时序图

```text
title 2. Stage 1 - Collect Source Items

participant Scheduler
participant RadarWorker
participant SourceConfig
participant SourceAdapter
participant ExternalSource
participant SharedDB

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 1. Collect source items
note right of RadarWorker: collect_source_items() ends only after all enabled sources finish\nExisting raw items are skipped, not updated

RadarWorker->SourceConfig: load enabled sources
SourceConfig-->RadarWorker: source_key, source_label, adapter, fetch config

loop each enabled source
  RadarWorker->SourceAdapter: fetch(config)
  note right of SourceAdapter: Adapter owns source-specific lookback / limit\nAdapter produces stable source_item_key

  SourceAdapter->ExternalSource: fetch source data
  alt transient request failure
    SourceAdapter->ExternalSource: retry with backoff\nup to 3 RPC attempts
  end
  ExternalSource-->SourceAdapter: source items and attachment URLs
  SourceAdapter-->RadarWorker: RawSourceItemCandidate[]

  loop each candidate
    RadarWorker->SharedDB: insert radar_raw_source_items\nunique(source_key, source_item_key)
    alt row inserted
      SharedDB-->RadarWorker: inserted
    else row already exists
      SharedDB-->RadarWorker: skipped
      note right of SharedDB: Existing raw item is not updated\nNo upsert in 1.0
    end
  end
end

RadarWorker->RadarWorker: Stage 1 completed
```

这张图描述 raw item 如何变成 Recent Policy Update，或者被标记为 discarded / failed。

```text
title 3. Stage 2 - Create Recent Policy Updates

participant Scheduler
participant RadarWorker
participant SharedDB
participant PDFDownloader
participant LLM

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 2. Create Recent Policy Updates
note right of RadarWorker: create_policy_updates() decides what belongs in the Radar feed\nEach raw item produces at most one policy update

RadarWorker->SharedDB: select raw items\npolicy_update_status in pending/failed\npolicy_update_attempt_count < 3

loop each selected raw item
  opt pdf_urls not empty
    RadarWorker->PDFDownloader: download and parse PDFs
    alt transient PDF failure
      PDFDownloader->PDFDownloader: retry with backoff\nup to 3 RPC attempts
    end

    alt PDF still failed
      PDFDownloader-->RadarWorker: failure
      RadarWorker->SharedDB: begin short transaction
      RadarWorker->SharedDB: set policy_update_status = failed\nincrement policy_update_attempt_count
      opt new policy_update_attempt_count reached 3
        RadarWorker->SharedDB: upsert radar_webhook_events\nattempt_exhausted for raw_source_item_id
      end
      RadarWorker->SharedDB: commit
      RadarWorker->RadarWorker: stop processing this raw item
    else PDF parsed
      PDFDownloader-->RadarWorker: parsed attachment context
    end
  end

  RadarWorker->LLM: filter + generate briefing\nraw_content + attachment context
  alt transient LLM failure
    LLM->LLM: retry with backoff\nup to 3 RPC attempts
  end
  LLM-->RadarWorker: ingest decision, briefing, and policy update fields

  alt invalid output or processing failed
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_update_status = failed\nincrement policy_update_attempt_count
    opt new policy_update_attempt_count reached 3
      RadarWorker->SharedDB: upsert radar_webhook_events\nattempt_exhausted for raw_source_item_id
    end
    RadarWorker->SharedDB: commit
    RadarWorker->RadarWorker: stop processing this raw item
  else should_ingest = false
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_update_status = discarded\nincrement policy_update_attempt_count
    RadarWorker->SharedDB: commit
    note right of RadarWorker: discard_reason is persisted\nfor prompt quality debugging
  else should_ingest = true
    RadarWorker->SharedDB: begin transaction
    RadarWorker->SharedDB: insert radar_policy_updates\nwrite source snapshot, briefing, and original_text\npolicy_extract_status = pending\npolicy_review_status = pending\naction_calculate_status = pending
    RadarWorker->SharedDB: set policy_update_status = ingested\nincrement policy_update_attempt_count
    RadarWorker->SharedDB: commit
  end
end

RadarWorker->RadarWorker: Stage 2 completed
```

### 表
```sql
CREATE TABLE radar_raw_source_items (
  id bigserial PRIMARY KEY,
  source_key text NOT NULL,
  source_label text NOT NULL,
  source_item_key text NOT NULL,
  source_url text NOT NULL,
  title text NOT NULL,
  published_at timestamptz,
  pdf_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
  raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw_content text NOT NULL,
  policy_update_status text NOT NULL DEFAULT 'pending',
  discard_reason text,
  policy_update_attempt_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_raw_source_items_source_item
    UNIQUE (source_key, source_item_key),
  CONSTRAINT chk_radar_raw_policy_update_status
    CHECK (policy_update_status IN ('pending', 'ingested', 'discarded', 'failed')),
  CONSTRAINT chk_radar_raw_policy_update_attempt_count
    CHECK (policy_update_attempt_count >= 0),
  CONSTRAINT chk_radar_raw_metadata_object
    CHECK (jsonb_typeof(raw_metadata) = 'object'),
  CONSTRAINT chk_radar_raw_pdf_urls_array
    CHECK (jsonb_typeof(pdf_urls) = 'array')
);
```

stage2 table:
```sql
CREATE TABLE radar_policy_updates (
  id bigserial PRIMARY KEY,
  raw_source_item_id bigint NOT NULL,
  source_key text NOT NULL,
  source_label text NOT NULL,
  source_url text NOT NULL,
  reference_number text,
  published_at timestamptz,
  pdf_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  headline text NOT NULL,
  summary text NOT NULL,
  briefing_markdown text NOT NULL,
  original_text text NOT NULL,
  effective_date date,
  policy_extract_status text NOT NULL DEFAULT 'pending',
  policy_extract_attempt_count integer NOT NULL DEFAULT 0,
  policy_review_status text NOT NULL DEFAULT 'pending',
  action_calculate_status text NOT NULL DEFAULT 'pending',
  action_calculate_attempt_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_policy_updates_raw_source_item
    UNIQUE (raw_source_item_id),
  CONSTRAINT chk_radar_policy_extract_status
    CHECK (policy_extract_status IN ('pending', 'succeeded', 'failed')),
  CONSTRAINT chk_radar_policy_review_status
    CHECK (policy_review_status IN ('pending', 'approved')),
  CONSTRAINT chk_radar_action_calculate_status
    CHECK (action_calculate_status IN ('pending', 'succeeded', 'failed')),
  CONSTRAINT chk_radar_policy_extract_attempt_count
    CHECK (policy_extract_attempt_count >= 0),
  CONSTRAINT chk_radar_action_calculate_attempt_count
    CHECK (action_calculate_attempt_count >= 0),
  CONSTRAINT chk_radar_policy_pdf_urls_array
    CHECK (jsonb_typeof(pdf_urls) = 'array'),
  CONSTRAINT chk_radar_policy_source_metadata_object
    CHECK (jsonb_typeof(source_metadata) = 'object')
);
```

##  状态机

### 1 `radar_raw_source_items.policy_update_status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 已抓取，尚未处理 | 是，若 `policy_update_attempt_count < 3` |
| `ingested` | 已通过过滤并创建 policy update | 否 |
| `discarded` | 已处理，但判断不应进入 Recent Updates | 否 |
| `failed` | 处理失败 | 是，若 `policy_update_attempt_count < 3` |

### 2 `radar_policy_updates.policy_extract_status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 尚未抽取 policy impact | 是，若 `policy_extract_attempt_count < 3` |
| `succeeded` | policy impact 已成功抽取并由黑盒存储 | 否 |
| `failed` | policy impact 抽取失败 | 是，若 `policy_extract_attempt_count < 3` |

### 3 `radar_policy_updates.policy_review_status`

| 枚举值 | 含义 | 后续行为 |
| --- | --- | --- |
| `pending` | 尚未审核过 | 当 `policy_extract_status = succeeded` 时可审核 |
| `approved` | 人工审核通过 | 允许进入 user action 计算 |

### 4 `radar_policy_updates.action_calculate_status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 尚未计算 user actions | 是，若 review approved 且 `action_calculate_attempt_count < 3` |
| `succeeded` | user actions 已成功计算并落库 | 否 |
| `failed` | user actions 计算失败 | 是，若 review approved 且 `action_calculate_attempt_count < 3` |


stage3 table:
```sql
CREATE TABLE radar_policy_impacts (
    id             bigserial PRIMARY KEY,
    policy_update_id        bigint      NOT NULL,
    hts_number     text        NOT NULL,
    impacted_type    text        NOT NULL,  -- deleted | inserted | measure_changed | desc_changed | rate_changed
    effective_time date,                  -- 变更生效日期（来自 measure.effective_start_date 或 HTS 变更日期）
    coos           text[],               -- 受影响原产国列表，仅 measure_changed 时有值，其他为 null
    row_desc text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_hts_affected_hts_number ON radar_policy_impacts (hts_number);
CREATE INDEX idx_hts_affected_news_id    ON radar_policy_impacts (policy_update_id);
```

stage3 sequece:
```text
title 4. Stage 3 - Create Policy Impacts for Review

participant Scheduler
participant RadarWorker
participant SharedDB
participant PolicyImpactBlackBox

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 3. Create policy impacts for review
note right of RadarWorker: create_policy_impacts() evaluates and persists structured impact data\nIt does not approve the result

RadarWorker->SharedDB: select policy updates\npolicy_extract_status in pending/failed\npolicy_extract_attempt_count < 3

loop each selected policy update
  RadarWorker->PolicyImpactBlackBox: extract_policy_impact(policy_update_id)
  PolicyImpactBlackBox->SharedDB: read radar_policy_updates
  PolicyImpactBlackBox->PolicyImpactBlackBox: evaluate policy scope, affected HTS, and tariff implications
  PolicyImpactBlackBox->SharedDB: persist policy impact in black-box tables
  PolicyImpactBlackBox-->RadarWorker: true / false

  alt extract succeeded
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_extract_status = succeeded\nincrement policy_extract_attempt_count
    RadarWorker->SharedDB: upsert radar_webhook_events\npolicy_impact_ready_for_review for policy_update_id
    RadarWorker->SharedDB: commit
  else extract failed
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_extract_status = failed\nincrement policy_extract_attempt_count
    opt new policy_extract_attempt_count reached 3
      RadarWorker->SharedDB: upsert radar_webhook_events\nattempt_exhausted for policy_update_id
    end
    RadarWorker->SharedDB: commit
  end
end

RadarWorker->RadarWorker: Stage 3 completed
```

stage 3 output.json
```json
{
    "source": {
        "type": "proclamation",
        "id": "10107",
        "url": "https://www.presidency.ucsb.edu/documents/proclamation-10107-...",
        "detected_at": "2026-05-07"
    },
    "hts_modifications": [
        {
            "deleted": [
                "9903.78.01",
                "9903.81.87",
                "9903.81.88",
                "9903.81.89",
                "9903.81.90",
                "9903.81.91",
                "9903.81.93",
                "9903.85.02",
                "9903.85.04",
                "9903.85.07",
                "9903.85.08"
            ],
            "inserted": [
                "9903.82.02",
                "7219.14.00.90"
            ]
        }
    ],
    "measures": [
        {
            "heading": "7219.14.00.90",
            "description": "Other",
            "unit_of_quantity": ["No."],
            "general_rate_of_duty": "20%",
            "special_rate_of_duty": "Free",
            "column_2_rate_of_duty": "45%",
            "quota_quantity": null,
            "additional_duties": null
        },
        {
            "heading": "9903.82.02",
            "note": 16,
            "description": "Except as provided for in headings 9903.82.14, 9903.85.67 and 9903.85.68, articles of aluminum, of steel or of copper and derivative aluminum or steel articles, as provided for in subdivisions (c)(i)-(v) of U.S. note 16",
            "ad_valorem_rate": 50.0,
            "value_basis": "CIF",
            "country_iso2": null,
            "melt_pour_origin_iso2": null,
            "is_potential": false,
            "effective_start_date": "2026-04-06",
            "excludes_headings": [
                "9903.82.14",
                "9903.85.67",
                "9903.85.68"
            ],
            "includes_headings": [
                "7601",
                "7604",
                "7605",
                "7606",
                "7607",
                "7608"
            ]
        }
    ]
}
```