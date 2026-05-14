# Compliance Radar BLB 1.0 数据库设计

最后更新：2026-05-14

本文档描述 Compliance Radar BLB 1.0 的数据库表结构设计。本文只覆盖 Radar 自有表；黑盒 Policy Impact 表由黑盒 owner 设计，不在本文中展开，但黑盒表必须能通过 `policy_update_id` 与 `radar_policy_updates.id` 关联。

数据库为 Postgres。所有 Radar 自有表建议使用 `radar_` 前缀。

## 1. 设计原则

- 每张 Radar 自有表都有整型自增主键 `id`。
- 每张表都有 `created_at` 和 `updated_at`。
- 时间戳字段默认使用 `timestamptz`。
- 日期字段如 policy/action 生效日期使用 `date`。
- 状态字段使用 `text` + check constraint，避免过早引入 Postgres enum 的 migration 成本。
- 表字段顺序遵循：主键、身份/关联字段、业务域快照、核心内容、状态/计数字段、时间戳。
- 数据库约束只覆盖幂等唯一键、状态枚举、计数非负和 JSON 根类型；不施加 DB-level 外键，复杂业务规则由应用层负责。
- 错误详情不入库；错误排查依赖应用日志。
- LLM request/response 不入库。
- `attempt_count` 是纯后端控制信号，不暴露给普通用户 API 或 review API。
- `source_key/source_label` 在 raw item 和 policy update 上都保存快照。
- 不作为查询条件的同一业务域只读补充字段，用 `metadata` 类 JSONB 字段聚合；本文只定义边界，不定义内部 key 结构。
- PDF 正文不入库，只保存 `pdf_urls`。
- Raw source item 入库语义是“如存在则跳过”，不是 upsert 覆盖。
- 所有 durable 重试都由外层周期任务按状态扫描回补；原子操作内部仍可做 RPC 级别短重试。

## 2. 表清单

| 表名 | 作用 | 主要写入方 | 主要读取方 |
| --- | --- | --- | --- |
| `radar_raw_source_items` | 保存从外部 source 抓取到的标准化原始条目 | `radar-backend` | `radar-backend` |
| `radar_policy_updates` | 保存进入 Recent Policy Updates 的政策更新 | `radar-backend`、`classification-backend` review API | `classification-backend`、`radar-backend`、黑盒 |
| `radar_user_actions` | 用户级 action 记录，一位用户对一个 policy update 最多一条；对应前端一张 action card | `radar-backend`、`classification-backend` completion API | `classification-backend` |
| `radar_notification_recipients` | 用户配置的邮件通知收件人 | `classification-backend` | `classification-backend`、`radar-backend` |
| `radar_email_deliveries` | 每封 Impact Action 邮件的发送记录与重试状态 | `radar-backend` | `radar-backend` |
| `radar_webhook_events` | 内部运营 webhook outbox，用于 review 提醒和 attempt exhausted 告警 | `radar-backend` | `radar-backend` |

## 3. 表关系总览

核心关系：

```text
radar_raw_source_items 1 -> 0..1 radar_policy_updates

radar_policy_updates 1 -> 0..N radar_user_actions
radar_user_actions N -> 1 users
radar_user_actions 1 -> N radar_email_deliveries

radar_notification_recipients N -> 1 users
radar_email_deliveries N -> 1 radar_notification_recipients

radar_webhook_events N -> operational entities
```

关系读法：

- `radar_policy_updates 1 -> 0..N radar_user_actions`：这里的 `N` 来自同一个 policy update 可能影响多个用户；每个目标用户最多生成一条 `radar_user_actions`。
- `radar_user_actions N -> 1 users`：这里的 `N` 来自同一个用户可能被多个 policy updates 影响，因此会拥有多条用户 action 记录。
- `radar_user_actions 1 -> N radar_email_deliveries`：这里的 `N` 来自同一条用户 action 需要发给该用户配置的多个 active recipients。
- `radar_email_deliveries N -> 1 radar_notification_recipients`：这里的 `N` 来自同一个 recipient 会随着不同 user actions 的产生，累计收到多封 action 邮件。
- `radar_webhook_events N -> operational entities`：这里的 `N` 来自 webhook event 可以指向不同处理单元，例如 raw item 转 policy update、policy impact 抽取、user action 计算、email delivery。

其中：

- `radar_policy_updates.raw_source_item_id` 对 `radar_raw_source_items.id` 加唯一约束，保证 1.0 中 raw item 与 policy update 一对一。
- 黑盒 policy impact 表由黑盒 owner 维护；Radar 自有表只保存 `radar_policy_updates` 上的抽取、审核与 user actions 计算状态。
- `radar_user_actions(user_id, policy_update_id)` 加唯一约束，保证一个用户对一个 policy update 最多一条 action 记录。
- `radar_email_deliveries(user_action_id, recipient_id)` 加唯一约束，保证同一 action 对同一个 recipient 最多一封邮件。
- `radar_webhook_events(event_type, entity_type, entity_id, channel)` 加唯一约束，保证同一个运营事件按实体去重。
- `radar_webhook_events` 的业务写入语义是 insert-if-absent：同一事件已存在时不更新。

关系约束边界：

- 本文中的关系是业务关系，不表示数据库外键。
- `user_id/completed_by` 指向 `classification-backend` 现有用户身份，当前按 `bigint` 存储，但不加数据库外键。
- `raw_source_item_id/policy_update_id/user_action_id/recipient_id` 都由应用层按业务流程维护一致性，不加数据库外键。
- 产品数据通过 `radar_user_actions.affected_products` 内的 `product_uid` 以快照形式保存。`classification-backend` 现有产品链路已经以 `product_uid` 作为产品身份；1.0 不加硬外键，避免产品表演进影响历史 action 展示与跳转。

## 4. 状态机

### 4.1 `radar_raw_source_items.policy_update_status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 已抓取，尚未处理 | 是，若 `policy_update_attempt_count < 3` |
| `ingested` | 已通过过滤并创建 policy update | 否 |
| `discarded` | 已处理，但判断不应进入 Recent Updates | 否 |
| `failed` | 处理失败 | 是，若 `policy_update_attempt_count < 3` |

### 4.2 `radar_policy_updates.policy_extract_status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 尚未抽取 policy impact | 是，若 `policy_extract_attempt_count < 3` |
| `succeeded` | policy impact 已成功抽取并由黑盒存储 | 否 |
| `failed` | policy impact 抽取失败 | 是，若 `policy_extract_attempt_count < 3` |

### 4.3 `radar_policy_updates.policy_review_status`

| 枚举值 | 含义 | 后续行为 |
| --- | --- | --- |
| `confirm_needed` | 需要人工确认或保持不推进 | 当 `policy_extract_status = succeeded` 时可确认 |
| `approved` | 人工审核通过 | 允许进入 user action 计算 |

### 4.4 `radar_policy_updates.action_calculate_status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 尚未计算 user actions | 是，若 review approved 且 `action_calculate_attempt_count < 3` |
| `succeeded` | user actions 已成功计算并落库 | 否 |
| `failed` | user actions 计算失败 | 是，若 review approved 且 `action_calculate_attempt_count < 3` |

### 4.5 `radar_user_actions.status`

| 枚举值 | 含义 |
| --- | --- |
| `action_needed` | 该用户 action 中至少还有一个 item 未完成 |
| `completed` | 该用户 action 下所有 action items 均已完成 |

### 4.6 `radar_user_actions.action_items[].status`

| 枚举值 | 含义 |
| --- | --- |
| `action_needed` | 该 JSON action item 未完成 |
| `completed` | 该 JSON action item 已完成 |

### 4.7 `radar_notification_recipients.status`

| 枚举值 | 含义 | 是否可用于新邮件 |
| --- | --- | --- |
| `active` | 当前有效收件人 | 是 |
| `unsubscribed` | 用户通过 unsubscribe 链接退订 | 否 |
| `deleted` | 用户在页面中删除 | 否 |

### 4.8 `radar_email_deliveries.status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 待发送 | 是，若 `attempt_count < 3` |
| `sent` | 已发送成功 | 否 |
| `failed` | 发送失败 | 是，若 `attempt_count < 3` |
| `skipped` | 发送前发现 recipient 已不再 active，因此跳过发送 | 否 |

### 4.9 `radar_webhook_events.event_type`

| 枚举值 | 含义 |
| --- | --- |
| `policy_impact_ready_for_review` | policy impact 已准备好，需要人工审核 |
| `attempt_exhausted` | 某条记录 durable attempt_count 达到上限，需要人工排查 |

### 4.10 `radar_webhook_events.status`

| 枚举值 | 含义 | 是否会继续自动处理 |
| --- | --- | --- |
| `pending` | 待发送 | 是，若 `attempt_count < 3` |
| `sent` | 已发送成功 | 否 |
| `failed` | 发送失败 | 是，若 `attempt_count < 3` |

### 4.11 Durable 重试扫描条件

周期任务的每个 stage 都是 state-driven：既处理本轮新产生的数据，也回补之前未完成或失败的数据。1.0 中 durable 层统一最多完成 3 次尝试，`attempt_count` 达到 3 后不再自动处理；是否人工修数据由数据库操作解决，不提供管理 API。

| Stage | 扫描条件 | 计数位置 |
| --- | --- | --- |
| Collect source items | 无 durable retry 表；source adapter 每轮按配置抓取窗口重新拉取 | 不入库 |
| Create policy updates | `radar_raw_source_items.policy_update_status IN ('pending', 'failed') AND policy_update_attempt_count < 3` | `radar_raw_source_items.policy_update_attempt_count` |
| Create policy impacts | `radar_policy_updates.policy_extract_status IN ('pending', 'failed') AND policy_extract_attempt_count < 3` | `radar_policy_updates.policy_extract_attempt_count` |
| Create user actions | `radar_policy_updates.policy_review_status = 'approved' AND action_calculate_status IN ('pending', 'failed') AND action_calculate_attempt_count < 3` | `radar_policy_updates.action_calculate_attempt_count` |
| Send action notification emails | `radar_email_deliveries.status IN ('pending', 'failed') AND attempt_count < 3` | `radar_email_deliveries.attempt_count` |
| Dispatch operational webhooks | `radar_webhook_events.status IN ('pending', 'failed') AND attempt_count < 3` | `radar_webhook_events.attempt_count` |

`attempt_count` 表示已经完成的 durable attempts。计数不在重型操作前递增，而是在操作返回后，和状态写回放在同一个短事务中递增。如果进程在操作中途崩溃，本次 attempt 不计数，后续仍按各 stage 的幂等或重复发送语义处理。

当某次处理失败，并且对应 durable attempt_count 已达到上限后，worker 尝试创建一条 `attempt_exhausted` webhook event。Webhook 发送由 Stage 6 按 `status/attempt_count` 状态驱动。

## 5. 表结构详情

### 5.1 `radar_raw_source_items`

保存外部数据源抓取到的标准化原始条目。source adapter 负责产出稳定的 `source_item_key`，worker 写入时如果 `(source_key, source_item_key)` 已存在则跳过，不覆盖旧记录。实现上可以使用 `INSERT ... ON CONFLICT DO NOTHING`。

| 字段 | 类型 | 必填 | 默认值 | 约束 / 索引 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | `bigserial` | 是 | 自增 | PK | 主键 |
| `source_key` | `text` | 是 |  | unique 组合项 | source 稳定标识，来自配置文件 |
| `source_label` | `text` | 是 |  |  | source 展示名称快照，来自配置文件 |
| `source_item_key` | `text` | 是 |  | unique 组合项 | adapter 产出的稳定去重键 |
| `source_url` | `text` | 是 |  |  | 原文 URL |
| `source_metadata` | `jsonb` | 是 | `'{}'::jsonb` | check object | source adapter 产出的只读补充快照，不作为 1.0 查询条件 |
| `source_title` | `text` | 是 |  |  | 源站原始标题 |
| `source_content` | `text` | 是 |  |  | 清洗后的正文文本，作为 LLM 主要输入 |
| `pdf_urls` | `jsonb` | 是 | `'[]'::jsonb` | check array | 附件 PDF URL 列表，不存 PDF 正文 |
| `reference_number` | `text` | 否 |  |  | 源站自己的可读编号，如 docket/document/notice number |
| `published_at` | `timestamptz` | 否 |  |  | 源站发布时间 |
| `policy_update_status` | `text` | 是 | `'pending'` | check enum；建议索引 | raw item 处理成 policy update 的状态 |
| `policy_update_attempt_count` | `integer` | 是 | `0` | check `>= 0`；建议索引 | raw item 处理 durable 尝试次数 |
| `created_at` | `timestamptz` | 是 | `now()` |  | 创建时间 |
| `updated_at` | `timestamptz` | 是 | `now()` |  | 更新时间 |

约束：

```sql
PRIMARY KEY (id)
UNIQUE (source_key, source_item_key)
CHECK (policy_update_status IN ('pending', 'ingested', 'discarded', 'failed'))
CHECK (policy_update_attempt_count >= 0)
CHECK (jsonb_typeof(source_metadata) = 'object')
CHECK (jsonb_typeof(pdf_urls) = 'array')
```

建议索引：

```sql
CREATE INDEX idx_radar_raw_source_items_processing
ON radar_raw_source_items (policy_update_status, policy_update_attempt_count, created_at);
```

### 5.2 `radar_policy_updates`

保存进入 Recent Policy Updates 的政策更新。每条 policy update 对应且只对应一条 raw source item。

| 字段 | 类型 | 必填 | 默认值 | 约束 / 索引 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | `bigserial` | 是 | 自增 | PK | 主键 |
| `raw_source_item_id` | `bigint` | 是 |  | unique | 对应的 raw source item |
| `source_key` | `text` | 是 |  | 建议索引 | source 稳定标识快照 |
| `source_label` | `text` | 是 |  |  | source 展示名称快照 |
| `source_url` | `text` | 是 |  |  | 原文 URL 快照 |
| `source_metadata` | `jsonb` | 是 | `'{}'::jsonb` | check object | source 域只读补充快照，不作为 1.0 查询条件 |
| `source_title` | `text` | 是 |  |  | 源站原始标题快照 |
| `source_content` | `text` | 是 |  |  | 来自 raw item `source_content` 的清洗正文，不由 LLM 生成 |
| `pdf_urls` | `jsonb` | 是 | `'[]'::jsonb` | check array | 附件 PDF URL 列表快照 |
| `reference_number` | `text` | 否 |  |  | 源站自己的可读编号快照，可为空 |
| `published_at` | `timestamptz` | 否 |  | 排序索引 | 源站发布时间 |
| `effective_date` | `date` | 否 |  |  | 政策级生效日期，可为空 |
| `headline` | `text` | 是 |  |  | policy update 标题 |
| `summary` | `text` | 是 |  |  | 列表摘要 |
| `briefing` | `text` | 是 |  |  | 详情页 briefing 文本 |
| `policy_extract_status` | `text` | 是 | `'pending'` | check enum；建议索引 | policy impact 抽取状态 |
| `policy_extract_attempt_count` | `integer` | 是 | `0` | check `>= 0` | policy impact 抽取 durable 尝试次数 |
| `policy_review_status` | `text` | 是 | `'confirm_needed'` | check enum；建议索引 | 人工审核状态 |
| `action_calculate_status` | `text` | 是 | `'pending'` | check enum；建议索引 | user actions 计算状态 |
| `action_calculate_attempt_count` | `integer` | 是 | `0` | check `>= 0` | user actions 计算 durable 尝试次数 |
| `created_at` | `timestamptz` | 是 | `now()` |  | 创建时间 |
| `updated_at` | `timestamptz` | 是 | `now()` |  | 更新时间 |

约束：

```sql
PRIMARY KEY (id)
UNIQUE (raw_source_item_id)
CHECK (policy_extract_status IN ('pending', 'succeeded', 'failed'))
CHECK (policy_review_status IN ('confirm_needed', 'approved'))
CHECK (action_calculate_status IN ('pending', 'succeeded', 'failed'))
CHECK (policy_extract_attempt_count >= 0)
CHECK (action_calculate_attempt_count >= 0)
CHECK (jsonb_typeof(pdf_urls) = 'array')
CHECK (jsonb_typeof(source_metadata) = 'object')
```

建议索引：

```sql
CREATE INDEX idx_radar_policy_updates_list
ON radar_policy_updates (published_at DESC NULLS LAST, created_at DESC);

CREATE INDEX idx_radar_policy_updates_source_list
ON radar_policy_updates (source_key, published_at DESC NULLS LAST, created_at DESC);

CREATE INDEX idx_radar_policy_updates_extract_work
ON radar_policy_updates (policy_extract_status, policy_extract_attempt_count, created_at);

CREATE INDEX idx_radar_policy_updates_action_work
ON radar_policy_updates (policy_review_status, action_calculate_status, action_calculate_attempt_count, created_at);
```

默认状态：

```text
policy_extract_status = pending
policy_review_status = confirm_needed
action_calculate_status = pending
```

审核约束由应用层保证：

```text
approve only if:
policy_extract_status = succeeded
AND policy_review_status = confirm_needed
```

1.0 不单独保存 `approved_by/approved_at`。Review API 使用 `classification-backend` 现有 admin/internal auth，不引入单独审核 token；如果未来需要严格审计，应新增 review audit 表，而不是复用 `updated_at`。

### 5.3 `radar_user_actions`

保存用户级 action 记录。一位用户对一个 policy update 最多一条记录；这条记录对应前端一张 action card。

| 字段 | 类型 | 必填 | 默认值 | 约束 / 索引 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | `bigserial` | 是 | 自增 | PK | 主键 |
| `user_id` | `bigint` | 是 |  | unique 组合项；建议索引 | 用户 ID |
| `policy_update_id` | `bigint` | 是 |  | unique 组合项 | 对应 policy update |
| `affected_products` | `jsonb` | 是 | `'[]'::jsonb` | check array | 受影响产品快照，用于展示和构造 action 跳转 |
| `action_items` | `jsonb` | 是 | `'[]'::jsonb` | check array | 建议 action items，最多包含 `reclassify_product` / `recalculate_tariff` |
| `status` | `text` | 是 | `'action_needed'` | check enum；建议索引 | 用户 action 聚合状态 |
| `completed_at` | `timestamptz` | 否 |  |  | 所有 items 完成时的时间；取消完成时清空 |
| `completed_by` | `bigint` | 否 |  |  | 完成该 card 的用户；1.0 通常是当前登录用户 |
| `created_at` | `timestamptz` | 是 | `now()` |  | 创建时间 |
| `updated_at` | `timestamptz` | 是 | `now()` |  | 更新时间 |

约束：

```sql
PRIMARY KEY (id)
UNIQUE (user_id, policy_update_id)
CHECK (status IN ('action_needed', 'completed'))
CHECK (jsonb_typeof(affected_products) = 'array')
CHECK (jsonb_typeof(action_items) = 'array')
```

建议索引：

```sql
CREATE INDEX idx_radar_user_actions_user_status
ON radar_user_actions (user_id, status, created_at DESC);
```

状态同步规则：

- 如果 `action_items` 中所有 items 都是 `completed`，则 `radar_user_actions.status = completed`。
- 如果任一 item 是 `action_needed`，则 `radar_user_actions.status = action_needed`。
- item completion API 必须在同一事务内更新 `action_items` JSON 与顶层 `status/completed_at/completed_by`。
- `affected_products` 和 `action_items` 的内部 shape 由应用层校验；数据库只做 JSON array 级别约束。

建议 user action payload shape：

```json
{
  "affected_products": [
    {
      "product_uid": "string",
      "product_name": "string",
      "hts_code": "string | null",
      "suggested_actions": ["reclassify_product", "recalculate_tariff"]
    }
  ],
  "action_items": [
    {
      "action_type": "reclassify_product",
      "effective_date": "YYYY-MM-DD | null",
      "status": "action_needed"
    }
  ]
}
```

其中 `action_items[].action_type` 最多两种且不可重复；`affected_products[].suggested_actions` 只能引用已有 action type。后端不做 `effective_date + 5 days` gating，该逻辑由前端决定。

### 5.4 `radar_notification_recipients`

保存用户配置的 Impact Action 邮件通知收件人。

| 字段 | 类型 | 必填 | 默认值 | 约束 / 索引 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | `bigserial` | 是 | 自增 | PK | 主键 |
| `user_id` | `bigint` | 是 |  | 建议索引 | 所属用户 |
| `email` | `text` | 是 |  | 建议唯一索引 | 收件邮箱，建议存 lowercase normalized email |
| `unsubscribe_token` | `text` | 是 |  | unique | 长期 unsubscribe token |
| `status` | `text` | 是 | `'active'` | check enum；建议索引 | recipient 状态 |
| `created_at` | `timestamptz` | 是 | `now()` |  | 创建时间 |
| `updated_at` | `timestamptz` | 是 | `now()` |  | 更新时间 |

约束：

```sql
PRIMARY KEY (id)
UNIQUE (unsubscribe_token)
CHECK (status IN ('active', 'unsubscribed', 'deleted'))
```

建议索引：

```sql
CREATE UNIQUE INDEX uq_radar_notification_recipients_user_email
ON radar_notification_recipients (user_id, lower(email))
WHERE status IN ('active', 'unsubscribed');

CREATE INDEX idx_radar_notification_recipients_user_status
ON radar_notification_recipients (user_id, status);
```

业务规则：

- 新增邮箱只做格式校验。
- 每个用户最多 5 个 active recipient。该限制由应用层在事务内检查。
- 已存在 active：返回 duplicate。
- 已存在 unsubscribed：不允许普通添加恢复。
- 已存在 deleted：可以重新激活为 active，倾向复用原 row 并刷新 token。
- 删除为软删除，设置 `status = deleted`。
- unsubscribe 设置 `status = unsubscribed`。

关于唯一索引：

- `active/unsubscribed` recipient 参与唯一约束，避免同一用户同一邮箱重复订阅或绕过 unsubscribe。
- `deleted` recipient 不参与唯一约束，因此删除后的邮箱可以重新添加。

### 5.5 `radar_email_deliveries`

保存每封 Impact Action 邮件的发送记录。发送由单实例 worker 串行处理，不使用数据库抢锁。

创建 delivery 时只针对当时 `active` 的 recipients。`unsubscribed/deleted` recipients 不进入 delivery 表；如果 delivery 创建后 recipient 状态变化，发送前再次检查并把未发送 delivery 标记为 `skipped`。

| 字段 | 类型 | 必填 | 默认值 | 约束 / 索引 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | `bigserial` | 是 | 自增 | PK | 主键 |
| `user_action_id` | `bigint` | 是 |  | unique 组合项 | 对应用户 action |
| `recipient_id` | `bigint` | 是 |  | unique 组合项 | 对应 recipient |
| `status` | `text` | 是 | `'pending'` | check enum；建议索引 | 邮件发送状态 |
| `attempt_count` | `integer` | 是 | `0` | check `>= 0`；建议索引 | durable 发送尝试次数 |
| `last_attempt_at` | `timestamptz` | 否 |  |  | 最近一次外部邮件发送调用返回后的写回时间；`skipped` 不设置 |
| `sent_at` | `timestamptz` | 否 |  |  | Email Provider accepted 后的写回时间；仅 `sent` 设置 |
| `created_at` | `timestamptz` | 是 | `now()` |  | 创建时间 |
| `updated_at` | `timestamptz` | 是 | `now()` |  | 更新时间 |

约束：

```sql
PRIMARY KEY (id)
UNIQUE (user_action_id, recipient_id)
CHECK (status IN ('pending', 'sent', 'failed', 'skipped'))
CHECK (attempt_count >= 0)
```

建议索引：

```sql
CREATE INDEX idx_radar_email_deliveries_send_work
ON radar_email_deliveries (status, attempt_count, created_at);
```

发送查询：

```sql
SELECT ...
FROM radar_email_deliveries
WHERE status IN ('pending', 'failed')
  AND attempt_count < 3
ORDER BY created_at
LIMIT 1
```

说明：

- 不存 `last_error`。
- 发送前重新检查 recipient 是否 active。
- 如果 recipient 已 unsubscribed/deleted，未发送 delivery 标记为 `skipped`，不递增 `attempt_count`，不设置 `last_attempt_at/sent_at`。
- Email Provider 调用通过 `EmailService` 在事务外发生；发送结果、`attempt_count` 递增和 `last_attempt_at` 放在同一个短事务中写回。
- 发送成功时设置 `status = sent` 和 `sent_at`；发送失败时设置 `status = failed`，不设置 `sent_at`。
- SMTP accepted 但进程在写回 `sent` 前崩溃，可能导致后续重复发送；1.0 接受该 best-effort 语义。

### 5.6 `radar_webhook_events`

保存内部运营 webhook outbox。1.0 用于两类通知：policy impact ready for review，以及 durable attempt_count 达到上限后的人工排查告警。实际发送渠道暂定为 Lark Team。

| 字段 | 类型 | 必填 | 默认值 | 约束 / 索引 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | `bigserial` | 是 | 自增 | PK | 主键 |
| `event_type` | `text` | 是 |  | unique 组合项；check enum | 事件类型 |
| `entity_type` | `text` | 是 |  | unique 组合项 | 关联处理单元，如 `policy_update` / `policy_impact` / `policy_extract` / `action_calculate` / `email_delivery` |
| `entity_id` | `bigint` | 是 |  | unique 组合项 | 关联实体 ID |
| `channel` | `text` | 是 | `'lark_team'` | unique 组合项 | 通知渠道 |
| `payload` | `jsonb` | 是 | `'{}'::jsonb` | check object | webhook payload 快照；内部结构由发送器契约定义 |
| `status` | `text` | 是 | `'pending'` | check enum；建议索引 | webhook 发送状态 |
| `attempt_count` | `integer` | 是 | `0` | check `>= 0`；建议索引 | durable 发送尝试次数 |
| `last_attempt_at` | `timestamptz` | 否 |  |  | 最近一次外部 webhook 调用返回后的写回时间 |
| `sent_at` | `timestamptz` | 否 |  |  | Lark accepted 后的写回时间；仅 `sent` 设置 |
| `created_at` | `timestamptz` | 是 | `now()` |  | 创建时间 |
| `updated_at` | `timestamptz` | 是 | `now()` |  | 更新时间 |

约束：

```sql
PRIMARY KEY (id)
UNIQUE (event_type, entity_type, entity_id, channel)
CHECK (event_type IN ('policy_impact_ready_for_review', 'attempt_exhausted'))
CHECK (entity_type IN ('policy_update', 'policy_impact', 'policy_extract', 'action_calculate', 'email_delivery'))
CHECK (jsonb_typeof(payload) = 'object')
CHECK (status IN ('pending', 'sent', 'failed'))
CHECK (attempt_count >= 0)
```

建议索引：

```sql
CREATE INDEX idx_radar_webhook_events_dispatch_work
ON radar_webhook_events (status, attempt_count, created_at);
```

发送规则：

- 业务 stage 只用 insert-if-absent 创建事件；同一事件已存在时不更新任何字段。
- 扫描 `status IN ('pending', 'failed') AND attempt_count < 3`。
- Lark webhook 调用通过 `WebhookService` 在事务外发生，内部 RPC retry 最多 3 次。
- 发送成功后用短事务设置 `status = sent`、`sent_at`、`last_attempt_at`，并递增 `attempt_count`。
- 发送失败后用短事务设置 `status = failed`、`last_attempt_at`，并递增 `attempt_count`；后续周期任务自然重试，直到 `attempt_count` 达到 3。
- Stage 6 发送后只用普通 `UPDATE` 推进 `status/attempt_count/last_attempt_at/sent_at`。

`entity_id` 指向对应处理单元的主记录：`policy_update` 使用 `radar_raw_source_items.id`，`policy_impact` / `policy_extract` / `action_calculate` 使用 `radar_policy_updates.id`，`email_delivery` 使用 `radar_email_deliveries.id`。

## 6. 关键事务边界

### 6.1 Raw Item 创建 Policy Update

同一事务内：

```text
insert radar_policy_updates
update radar_raw_source_items.policy_update_status = ingested
```

如果事务失败，则 raw item 留在 `failed` 或当前状态，后续按 attempt_count 规则回补。

### 6.2 Action 计算落库

同一事务内：

```text
insert radar_user_actions with affected_products and action_items JSON for accumulated candidates
insert radar_email_deliveries for active recipients
update radar_policy_updates.action_calculate_status = succeeded
```

如果计算失败，设置 `action_calculate_status = failed`，不写入 user actions 或 email deliveries。若失败后 `action_calculate_attempt_count` 已达到上限，则尝试创建 `attempt_exhausted` webhook event。

邮件发送必须在事务提交后进行。

### 6.3 Action Completion

同一事务内：

```text
verify action belongs to current user
update radar_user_actions.action_items JSON
recompute parent radar_user_actions.status
update radar_user_actions completed_at/completed_by if needed
```

### 6.4 Email Delivery 发送

邮件发送不使用长事务：

```text
select one eligible delivery
short transaction: check recipient active
send email outside transaction via EmailService
short transaction: write skipped or send result fields
```

如果 recipient 已不是 active，短事务标记未发送 delivery 为 `skipped`。发送前检查是 best-effort；检查后立即 unsubscribe 的极小竞态本期接受。Email Provider 调用必须设置严格 timeout。

### 6.5 Operational Webhook 发送

Lark webhook 发送不使用长事务：

```text
select one eligible webhook event
send Lark webhook outside transaction via WebhookService
short transaction: update webhook event status, attempt_count, last_attempt_at, sent_at
```

## 7. 约束汇总

### 7.1 Check Constraints

```sql
ALTER TABLE radar_raw_source_items
  ADD CONSTRAINT chk_radar_raw_policy_update_status
  CHECK (policy_update_status IN ('pending', 'ingested', 'discarded', 'failed')),
  ADD CONSTRAINT chk_radar_raw_policy_update_attempt_count
  CHECK (policy_update_attempt_count >= 0),
  ADD CONSTRAINT chk_radar_raw_source_metadata_object
  CHECK (jsonb_typeof(source_metadata) = 'object'),
  ADD CONSTRAINT chk_radar_raw_pdf_urls_array
  CHECK (jsonb_typeof(pdf_urls) = 'array');

ALTER TABLE radar_policy_updates
  ADD CONSTRAINT chk_radar_policy_extract_status
  CHECK (policy_extract_status IN ('pending', 'succeeded', 'failed')),
  ADD CONSTRAINT chk_radar_policy_review_status
  CHECK (policy_review_status IN ('confirm_needed', 'approved')),
  ADD CONSTRAINT chk_radar_action_calculate_status
  CHECK (action_calculate_status IN ('pending', 'succeeded', 'failed')),
  ADD CONSTRAINT chk_radar_policy_extract_attempt_count
  CHECK (policy_extract_attempt_count >= 0),
  ADD CONSTRAINT chk_radar_action_calculate_attempt_count
  CHECK (action_calculate_attempt_count >= 0),
  ADD CONSTRAINT chk_radar_policy_pdf_urls_array
  CHECK (jsonb_typeof(pdf_urls) = 'array'),
  ADD CONSTRAINT chk_radar_policy_source_metadata_object
  CHECK (jsonb_typeof(source_metadata) = 'object');

ALTER TABLE radar_user_actions
  ADD CONSTRAINT chk_radar_user_actions_status
  CHECK (status IN ('action_needed', 'completed')),
  ADD CONSTRAINT chk_radar_user_actions_affected_products_array
  CHECK (jsonb_typeof(affected_products) = 'array'),
  ADD CONSTRAINT chk_radar_user_actions_action_items_array
  CHECK (jsonb_typeof(action_items) = 'array');

ALTER TABLE radar_notification_recipients
  ADD CONSTRAINT chk_radar_notification_recipients_status
  CHECK (status IN ('active', 'unsubscribed', 'deleted'));

ALTER TABLE radar_email_deliveries
  ADD CONSTRAINT chk_radar_email_deliveries_status
  CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
  ADD CONSTRAINT chk_radar_email_deliveries_attempt_count
  CHECK (attempt_count >= 0);

ALTER TABLE radar_webhook_events
  ADD CONSTRAINT chk_radar_webhook_events_event_type
  CHECK (event_type IN ('policy_impact_ready_for_review', 'attempt_exhausted')),
  ADD CONSTRAINT chk_radar_webhook_events_entity_type
  CHECK (entity_type IN ('policy_update', 'policy_impact', 'policy_extract', 'action_calculate', 'email_delivery')),
  ADD CONSTRAINT chk_radar_webhook_events_status
  CHECK (status IN ('pending', 'sent', 'failed')),
  ADD CONSTRAINT chk_radar_webhook_events_payload_object
  CHECK (jsonb_typeof(payload) = 'object'),
  ADD CONSTRAINT chk_radar_webhook_events_attempt_count
  CHECK (attempt_count >= 0);
```

### 7.2 Unique Constraints / Indexes

```sql
ALTER TABLE radar_raw_source_items
  ADD CONSTRAINT uq_radar_raw_source_items_source_item
  UNIQUE (source_key, source_item_key);

ALTER TABLE radar_policy_updates
  ADD CONSTRAINT uq_radar_policy_updates_raw_source_item
  UNIQUE (raw_source_item_id);

ALTER TABLE radar_user_actions
  ADD CONSTRAINT uq_radar_user_actions_user_policy
  UNIQUE (user_id, policy_update_id);

ALTER TABLE radar_email_deliveries
  ADD CONSTRAINT uq_radar_email_deliveries_action_recipient
  UNIQUE (user_action_id, recipient_id);

ALTER TABLE radar_notification_recipients
  ADD CONSTRAINT uq_radar_notification_recipients_unsubscribe_token
  UNIQUE (unsubscribe_token);

CREATE UNIQUE INDEX uq_radar_notification_recipients_user_email
ON radar_notification_recipients (user_id, lower(email))
WHERE status IN ('active', 'unsubscribed');

ALTER TABLE radar_webhook_events
  ADD CONSTRAINT uq_radar_webhook_events_event_entity_channel
  UNIQUE (event_type, entity_type, entity_id, channel);
```

## 8. 后续实现注意事项

以下事项不改变当前数据库主结构，后续实现时按需处理：

1. `source_metadata` 暂不建 GIN 索引。只有实现中确实出现 metadata 查询条件时再补。
2. recipient email 使用 `lower(email)` partial unique index，不引入 `citext` extension。
3. `pdf_urls` 当前按 `jsonb` array 设计，元素先按 URL 字符串处理；如果实现时需要更多附件元信息，可扩展为 object array，不需要改字段类型。
4. `product_uid`、`user_id`、`policy_update_id` 等关系字段不加硬外键，但 API 实现需要和 classification/sandbox 现有权限与数据一致性规则保持一致。
