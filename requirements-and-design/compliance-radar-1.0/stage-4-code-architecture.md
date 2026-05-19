# Stage 4 - Create User Actions Code Architecture

最后更新：2026-05-20

Stage 4 把已审核通过的 policy impact 转成用户级 Radar action card。它不执行 reclassification，不执行 tariff recalculation，只创建产品内可展示、可通知、可完成的 action context。

本文档是编码指导，不保留讨论过程。

## 1. 固定边界

Stage 4 的 policy impact 输入只认 `radar_policy_impacts`。`impact_json`、`scope_sets`、`measures` 的解析和展平由 approve 流程负责，不属于 Stage 4。

Stage 4 假设 `policy_review_status = approved` 时，对应的 `radar_policy_impacts` 已经由 approve 流程准备完成，且同一个 `policy_update_id` 下不会重复生成 impacts。Stage 4 不修复 approve 半成品；如果 approved policy update 没有 impacts，按 action calculation failure 处理。

Stage 4 不写复杂业务 SQL。SQL 只拉取简单数据；HTS prefix match、COO match、action type 映射、产品聚合、用户聚合都在 Python 中完成。

Stage 4 不解释 classification workflow。只要产品未删除、非 split parent、classification type 为 HTS，并且存在有效 10 位 `t_product_hts_candidate.hts_code_normalized`，就可以进入 Radar 匹配。`workflow_status`、`product_source`、`t_product.hts_code` 不参与过滤。

`t_product_hts_candidate.hts_code_normalized` 是唯一 HTS 匹配来源。`t_product` 只提供产品边界、`user_id` 和展示字段。

这条边界是刻意选择：Stage 4 不复用 sandbox UI 的可见性规则，不只看 selected/current HTS，也不把 saved COO 纳入 1.0。Radar 1.0 更偏向“有效 candidate 可能受影响就提醒”，避免因为 UI 状态或用户尚未保存选择而漏报。

`radar_policy_impacts.effective_time` 不参与匹配，只用于 action item 的 `effective_date`。

COO 只使用：

- `t_sandbox_calculation_result.country_code`
- `t_sandbox_product_profile.imported_country_code`

`t_user_saved_coo` 不参与 1.0 匹配。

`radar_policy_impacts.coos` 暂按原始 token 匹配，不展开 `A` / `D` / `E` / `P` / `S` 这类 HTS special rate program code。未来如果补映射表，只改 COO normalize，不改匹配主流程。

## 2. 主流程

```python
class CreateUserActionsStage:
    name = "create_user_actions"

    def run(self, context: WorkerContext) -> StageResult:
        logger.info("stage invoked: name=%s run_id=%s", self.name, context.run_id)

        policy_updates = _list_policy_updates_to_calculate_user_actions()
        logger.info(
            "policy updates selected for action calculation: count=%s run_id=%s",
            len(policy_updates),
            context.run_id,
        )
        if not policy_updates:
            return StageResult()

        product_match_data = _load_product_match_data()

        for policy_update in policy_updates:
            try:
                _create_user_actions(policy_update, product_match_data)
            except Exception:
                logger.exception(
                    "create user actions failed: policy_update_id=%s",
                    policy_update["id"],
                )
                try:
                    _mark_failed(policy_update)
                except Exception:
                    logger.exception(
                        "create user actions failed and failed to mark failed: policy_update_id=%s",
                        policy_update["id"],
                    )

        return StageResult()
```

`_load_product_match_data()` 在本轮 Stage run 中只执行一次，结果复用于本轮所有 policy updates。如果它失败，整轮 Stage 4 失败退出，不更新任何 `radar_policy_updates`，也不递增任何 `action_calculate_attempt_count`。

只有进入单个 policy update 处理后发生的失败，才递增该 policy update 的 attempt count。

```python
def _list_policy_updates_to_calculate_user_actions() -> list[PolicyUpdateModel]:
    with acquire_connection() as conn:
        return policy_updates_repository.list_policy_updates_to_calculate_user_actions(conn)
```

`_mark_failed()` 自身失败时只记录日志，不阻断后续 policy update。Stage 4 要尽量把单条 policy update 的坏状态收敛在单条记录上。

```python
def _create_user_actions(policy_update: PolicyUpdateModel, product_match_data: ProductMatchData) -> None:
    impacts = _load_and_normalize_impacts(policy_update)
    candidates = _calculate_user_action_candidates(
        policy_update=policy_update,
        impacts=impacts,
        product_match_data=product_match_data,
    )
    _commit_user_actions(policy_update, candidates)
```

## 3. Product Match Data

`ProductMatchData` 是 Stage 4 匹配算法的输入集合。

```python
class ProductCandidate(TypedDict):
    user_id: int
    account_owner_email: str | None
    product_uid: str
    product_name: str
    hts_code: str
    hts_code_normalized: str
    candidate_rank: int | None


class TariffCalculationCoo(TypedDict):
    product_uid: str
    hts_code_normalized: str
    country_code: str


class ProductImportedCoo(TypedDict):
    product_uid: str
    country_code: str


class ProductMatchData(TypedDict):
    product_candidates: list[ProductCandidate]
    calculation_coos_by_product_uid: dict[str, list[TariffCalculationCoo]]
    imported_coos_by_product_uid: dict[str, list[ProductImportedCoo]]
```

```python
def _load_product_match_data() -> ProductMatchData:
    with acquire_connection() as conn:
        product_candidates = product_match_repository.list_product_candidates(conn)
        calculation_coos = product_match_repository.list_calculation_coos(conn)
        imported_coos = product_match_repository.list_imported_coos(conn)

    return ProductMatchData(
        product_candidates=product_candidates,
        calculation_coos_by_product_uid=group_calculation_coos(calculation_coos),
        imported_coos_by_product_uid=group_imported_coos(imported_coos),
    )
```

### Product Candidates

```python
product_match_repository.list_product_candidates(conn) -> list[ProductCandidate]
```

SQL shape:

```sql
SELECT
  p.user_id,
  u.email AS account_owner_email,
  p.product_uid,
  COALESCE(p.display_name, p.product_name) AS product_name,
  COALESCE(NULLIF(c.hts_code, ''), c.hts_code_normalized) AS hts_code,
  c.hts_code_normalized,
  c.candidate_rank
FROM t_product p
JOIN t_product_hts_candidate c ON c.product_uid = p.product_uid
JOIN users u ON u.id = p.user_id
WHERE p.is_deleted = false
  AND p.is_split_parent = false
  AND p.classification_type = 'hts'
  AND c.hts_code_normalized ~ '^[0-9]{10}$'
ORDER BY
  p.user_id ASC,
  p.product_uid ASC,
  c.candidate_rank ASC NULLS LAST,
  c.hts_code_normalized ASC;
```

`users.email` 只用于 email footer 的账号拥有者识别。缺失时不写入 `EmailDeliveryPayload.account_owner_email`，不阻断 action 生成。

`ProductCandidate` 的一行表示一个产品的一个 HTS candidate，不是一个产品。`product_uid` 会重复；`product_uid + hts_code_normalized` 才是匹配输入的候选粒度。

### Calculation COO

```python
product_match_repository.list_calculation_coos(conn) -> list[TariffCalculationCoo]
```

SQL shape:

```sql
SELECT
  product_uid,
  hts_code_normalized,
  upper(trim(country_code)) AS country_code
FROM t_sandbox_calculation_result
WHERE hts_code_normalized ~ '^[0-9]{10}$'
  AND country_code IS NOT NULL
  AND trim(country_code) <> '';
```

Calculation COO 必须保留 `hts_code_normalized`。它表示用户实际计算过某个产品在某个 HTS 下的 COO。

### Imported COO

```python
product_match_repository.list_imported_coos(conn) -> list[ProductImportedCoo]
```

SQL shape:

```sql
SELECT
  product_uid,
  upper(trim(imported_country_code)) AS country_code
FROM t_sandbox_product_profile
WHERE imported_country_code IS NOT NULL
  AND trim(imported_country_code) <> '';
```

Imported COO 是产品导入时自带的 COO，只和当前已经命中的 product candidate 配合判断。

## 4. Policy Impacts

```python
policy_impacts_repository.list_by_policy_update_id(conn, *, policy_update_id: int) -> list[PolicyImpactModel]
```

需要读取：

- `id`
- `policy_update_id`
- `hts_number`
- `impacted_type`
- `effective_time`
- `coos`

`radar_policy_impacts.impacted_type` 映射：

| impacted_type | action types |
| --- | --- |
| `deleted` | `reclassify_product`, `recalculate_tariff` |
| `inserted` | none |
| `measure_changed` | `recalculate_tariff` |
| `desc_changed` | `reclassify_product`, `recalculate_tariff` |
| `rate_changed` | `recalculate_tariff` |

```python
def _load_and_normalize_impacts(policy_update: PolicyUpdateModel) -> list[NormalizedImpact]:
    with acquire_connection() as conn:
        rows = policy_impacts_repository.list_by_policy_update_id(
            conn,
            policy_update_id=policy_update["id"],
        )

    if not rows:
        raise ActionCalculationError("approved policy update has no policy impacts")

    impacts: list[NormalizedImpact] = []
    for row in rows:
        impacted_type = row["impacted_type"]
        if impacted_type == "inserted":
            continue
        if impacted_type not in {"deleted", "measure_changed", "desc_changed", "rate_changed"}:
            raise ActionCalculationError(f"unknown impacted_type: impact_id={row['id']}")

        hts_prefix = digits_only(row["hts_number"])
        if len(hts_prefix) not in {2, 4, 6, 8, 10}:
            raise ActionCalculationError(f"invalid hts_number: impact_id={row['id']}")
        coos = normalize_coos(row["coos"])
        if impacted_type != "measure_changed" and coos:
            raise ActionCalculationError(f"unexpected coos: impact_id={row['id']}")

        impacts.append(
            NormalizedImpact(
                id=row["id"],
                impacted_type=impacted_type,
                hts_prefix=hts_prefix,
                coos=coos,
                effective_date=row["effective_time"],
            )
        )

    return impacts
```

`inserted` 是正常无动作，直接跳过。如果某个 policy update 的 impacts 全部是 `inserted`，返回空列表，后续仍标记 `action_calculate_status = succeeded`。

其他异常数据应 fail 当前 policy update：

- 非 inserted impact 的 `hts_number` 归一化后长度不是 `2 / 4 / 6 / 8 / 10`。
- `impacted_type` 未知。
- 原始 `coos` 数组非空，但 normalize 后为空 set。
- 非 `measure_changed` impact 带有非空 `coos`。

`normalize_coos()`：

- `null` / 空数组 => 空 set，表示无 COO 限制。
- 非空 token trim + upper 后保留。
- 空字符串丢弃。
- 重复值去重。
- 不展开 special rate program code。

`NormalizedImpact.effective_date` 只保留 `radar_policy_impacts.effective_time`。不要在这里 fallback 到 `radar_policy_updates.effective_date`；fallback 必须在 action type 聚合阶段统一处理。

## 5. Matching Algorithm

匹配输入的细粒度是：

```text
product_uid x hts_candidate x policy_impact
```

输出聚合到：

```text
user_id x product_uid
```

主算法：

```python
def _calculate_user_action_candidates(
    *,
    policy_update: PolicyUpdateModel,
    impacts: list[NormalizedImpact],
    product_match_data: ProductMatchData,
) -> list[UserActionCandidate]:
    impacts_by_prefix = group_impacts_by_prefix(impacts)
    builders: dict[int, UserActionBuilder] = {}

    for product in product_match_data.product_candidates:
        matched_impacts: list[ProductImpactMatch] = []

        for hts_prefix in prefixes_of(product.hts_code_normalized):
            for impact in impacts_by_prefix.get(hts_prefix, []):
                if impact.impacted_type == "measure_changed" and impact.coos:
                    if not _has_matching_product_coo(
                        impact=impact,
                        product=product,
                        calculation_coos=product_match_data.calculation_coos_by_product_uid.get(product.product_uid, []),
                        imported_coos=product_match_data.imported_coos_by_product_uid.get(product.product_uid, []),
                    ):
                        continue

                matched_impacts.append(ProductImpactMatch(product=product, impact=impact))

        if not matched_impacts:
            continue

        builder = builders.setdefault(
            product.user_id,
            UserActionBuilder(
                user_id=product.user_id,
                account_owner_email=product.account_owner_email,
            ),
        )
        builder.add_product_matches(product.product_uid, matched_impacts)

    return [builder.to_candidate(policy_update) for builder in builders.values()]
```

Prefix lookup:

```python
def prefixes_of(hts_code_normalized: str) -> list[str]:
    return [hts_code_normalized[:i] for i in range(1, len(hts_code_normalized) + 1)]
```

先按 `impact.hts_prefix` 分组，再用产品 10 位 HTS 的所有前缀查找 impacts，避免 `product candidates x impacts` 全量嵌套。

COO match:

```python
def _has_matching_product_coo(
    *,
    impact: NormalizedImpact,
    product: ProductCandidate,
    calculation_coos: list[TariffCalculationCoo],
    imported_coos: list[ProductImportedCoo],
) -> bool:
    for row in calculation_coos:
        if (
            row.hts_code_normalized.startswith(impact.hts_prefix)
            and row.country_code in impact.coos
        ):
            return True

    return any(row.country_code in impact.coos for row in imported_coos)
```

如果 `measure_changed.coos` 为空，HTS prefix match 即命中。

如果 `measure_changed.coos` 非空：

- calculation COO 必须同时命中 HTS prefix 和 COO。
- imported COO 只在当前 product candidate 已命中 HTS prefix 的前提下判断 COO。
- calculation COO 和 imported COO 是并集语义，不互相覆盖；任一来源命中即可。

## 6. Aggregation

Aggregation 由 `UserActionBuilder.add_product_matches()` 和 `UserActionBuilder.to_candidate()` 承载。前者按 product 合并匹配结果，后者构造最终 `UserActionCandidate`。

```python
ACTION_TYPE_ORDER = [
    ActionType.RECLASSIFY_PRODUCT,
    ActionType.RECALCULATE_TARIFF,
]


class UserActionBuilder:
    def add_product_matches(self, product_uid: str, matches: list[ProductImpactMatch]) -> None:
        # merge product by product_uid
        # union suggested actions
        # keep matched candidate data for display HTS selection
        # track effective_date candidates by action type

    def to_candidate(self, policy_update: PolicyUpdateModel) -> UserActionCandidate:
        # build affected_products
        # build action_items
        # build email payload
```

`affected_products` 中每个 `product_uid` 只出现一次。

```json
{
  "product_uid": "string",
  "product_name": "string",
  "hts_code": "string",
  "suggested_actions": ["reclassify_product", "recalculate_tariff"]
}
```

同一产品命中多个 candidate / impact 时：

- `suggested_actions` 做 union。
- `hts_code` 从命中本次 policy impact 的 candidates 中选择。
- 展示 HTS 排序：`candidate_rank ASC NULLS LAST`, `hts_code_normalized ASC`。
- `product_name` 使用 `COALESCE(display_name, product_name)`。

`builder.add_product_matches()` 必须按 `product_uid` merge/upsert，不得 append 出重复产品。`suggested_actions`、`action_items` 和 `action_summaries.product_count` 都按 product/action type 去重聚合。

所有 action type 数组都按 `ACTION_TYPE_ORDER` 输出，包括 `affected_products[].suggested_actions`、`action_items` 和 `email_payload.action_summaries`。

`affected_products` 输出保持稳定顺序，沿用 product candidate SQL 顺序下的 first-seen order。

`action_items` 按 action type 聚合。

```json
{
  "action_type": "recalculate_tariff",
  "effective_date": "YYYY-MM-DD | null",
  "status": "action_needed"
}
```

`effective_date`：

1. 使用命中该 action type 的最早非空 `radar_policy_impacts.effective_time`。
2. fallback 到 `radar_policy_updates.effective_date`。
3. fallback 到 `null`。

写入 `action_items` 或 email payload 前，`effective_time` / `effective_date` 必须统一转成 ISO date string；不要把 Python `date` / `datetime` 对象直接塞进 JSONB。

`EmailDeliveryPayload` 由 Stage 4 创建快照：

```python
payload = {
    "source_label": policy_update["source_label"],
    "reference_number": policy_update["reference_number"],
    "headline": policy_update["headline"],
    "summary": policy_update["summary"],
    "source_url": policy_update["source_url"],
    "affected_products": [
        {
            "product_name": product["product_name"],
            "hts_code": product["hts_code"],
        }
        for product in candidate.affected_products
    ],
    "action_summaries": build_action_summaries(candidate),
}

if candidate.account_owner_email:
    payload["account_owner_email"] = candidate.account_owner_email
```

`action_summaries.product_count` 按 product 去重计数，不按 candidate 或 impact 计数。

没有 active recipient 的用户仍然创建 user action。邮件只是通知通道，不影响 action card 落库。

## 7. Commit Transaction

事务粒度是 `policy_update_id`。

```python
def _commit_user_actions(
    policy_update: PolicyUpdateModel,
    candidates: list[UserActionCandidate],
) -> None:
    with acquire_connection_with_transaction() as conn:
        for candidate in candidates:
            user_action_id = user_actions_repository.create_user_action(
                conn,
                user_id=candidate.user_id,
                policy_update_id=policy_update["id"],
                affected_products=candidate.affected_products,
                action_items=candidate.action_items,
            )

            if user_action_id is None:
                user_action_id = user_actions_repository.get_id_by_user_and_policy_update(
                    conn,
                    user_id=candidate.user_id,
                    policy_update_id=policy_update["id"],
                )
                if user_action_id is None:
                    raise ActionCalculationError("existing user action not found after conflict")

            recipients = notification_recipients_repository.list_active_recipients_by_user_id(
                conn,
                user_id=candidate.user_id,
            )

            for recipient in recipients:
                email_deliveries_repository.create_email_delivery(
                    conn,
                    user_action_id=user_action_id,
                    recipient_id=recipient["id"],
                    payload=candidate.email_payload,
                )

        rowcount = policy_updates_repository.mark_action_calculate_succeeded(
            conn,
            id=policy_update["id"],
        )
        if rowcount != 1:
            raise ActionCalculationError("policy update not found while marking succeeded")
```

同一条 policy update 下的所有 user actions、email deliveries、`action_calculate_status = succeeded` 必须在同一个事务内提交。

如果 `candidates` 为空，也要 mark succeeded，表示 action calculation 已完成，只是没有用户受影响。

`mark_action_calculate_succeeded()` 会同步记录本次 durable attempt。

幂等规则：

- `create_user_action()` 是 create-if-absent：插入成功返回 id，唯一键已存在返回 `None`。
- `create_email_delivery()` 是 create-if-absent：插入成功返回 id，唯一键已存在返回 `None`。
- existing action 的 `affected_products` / `action_items` 不更新。
- 重复执行只做幂等防御，不引入 action 重算语义。

## 8. Failure Handling

单个 policy update 处理失败时：

```python
def _mark_failed(policy_update: PolicyUpdateModel) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = policy_updates_repository.mark_action_calculate_failed(
            conn,
            id=policy_update["id"],
        )
        if rowcount != 1:
            raise ActionCalculationError("policy update not found while marking failed")

        updated_policy_update = policy_updates_repository.get_by_id(
            conn,
            id=policy_update["id"],
        )
        if updated_policy_update is None:
            raise ActionCalculationError("policy update not found after marking failed")

        if updated_policy_update["action_calculate_attempt_count"] >= 3:
            webhook_events_repository.create_webhook_event(
                conn,
                event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                entity_type=WebhookEntityType.ACTION_CALCULATE,
                entity_id=policy_update["id"],
                payload={
                    "reason": "action_calculate_failed",
                    "stage": "create_user_actions",
                    "source_label": policy_update["source_label"],
                    "reference_number": policy_update["reference_number"],
                    "headline": policy_update["headline"],
                    "source_url": policy_update["source_url"],
                    "attempt_count": updated_policy_update["action_calculate_attempt_count"],
                },
            )
```

失败路径不写部分 user actions。所有 action 写入都只发生在成功提交事务里。

`mark_action_calculate_failed()` 会同步记录本次 durable attempt。若需要判断是否触发 attempt exhausted webhook，更新后通过 `get_by_id()` 读取最新 attempt count。

失败日志要能区分上游数据契约问题、产品匹配数据加载问题和写库问题。由于 Stage 4 使用全量内存匹配，正常日志也应保留必要的输入规模、命中规模和耗时信息，方便定位慢任务或异常低命中。

## 9. Required Repository Methods

### `policy_updates_repository`

```python
list_policy_updates_to_calculate_user_actions(conn) -> list[PolicyUpdateModel]

get_by_id(conn, *, id: int) -> PolicyUpdateModel | None

mark_action_calculate_succeeded(conn, *, id: int) -> int

mark_action_calculate_failed(conn, *, id: int) -> int
```

### `policy_impacts_repository`

```python
list_by_policy_update_id(conn, *, policy_update_id: int) -> list[PolicyImpactModel]
```

### `product_match_repository`

```python
list_product_candidates(conn) -> list[ProductCandidate]
list_calculation_coos(conn) -> list[TariffCalculationCoo]
list_imported_coos(conn) -> list[ProductImportedCoo]
```

### `user_actions_repository`

```python
create_user_action(
    conn,
    *,
    user_id: int,
    policy_update_id: int,
    affected_products: list[AffectedProduct],
    action_items: list[ActionItem],
) -> int | None

get_id_by_user_and_policy_update(conn, *, user_id: int, policy_update_id: int) -> int | None
```

### `notification_recipients_repository`

```python
list_active_recipients_by_user_id(conn, *, user_id: int) -> list[NotificationRecipientModel]
```

### `email_deliveries_repository`

```python
create_email_delivery(
    conn,
    *,
    user_action_id: int,
    recipient_id: int,
    payload: EmailDeliveryPayload,
) -> int | None
```

### `webhook_events_repository`

```python
create_webhook_event(
    conn,
    *,
    event_type: WebhookEventType,
    entity_type: WebhookEntityType,
    entity_id: int,
    payload: WebhookPayload,
) -> int | None
```

## 10. Database Notes

`radar_policy_impacts.policy_update_id` 需要索引。

`radar_policy_impacts.hts_number` 暂不建索引。Stage 4 不使用数据库侧 HTS 匹配；未来如果需要数据库侧粗过滤，再重新设计 normalized/generated index。

## 11. Not In 1.0

- 不使用 `t_user_saved_coo`。
- 不复用 sandbox UI 可见性规则。
- 不只匹配 selected/current HTS candidate。
- 不展开 HTS special rate program code 到国家集合。
- 不按 `radar_policy_impacts.effective_time` 过滤产品。
- 不用数据库侧 HTS prefix join。
- 不做 Stage 4 多线程。
- 不做 action 重算或 existing action payload 更新。
