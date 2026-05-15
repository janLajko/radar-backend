# Compliance Radar BLB 1.0 时序图

最后更新：2026-05-14

本文档使用 https://sequencediagram.org/ 支持的文本格式编写。每个代码块是一张独立时序图，可以单独复制到 sequencediagram.org 中渲染。

## 1. 周期任务总览

这张图表达 Radar 主流程的外层编排，以及 policy impact 人工审核这个必经 gate。它只说明每一步的目的；各 stage 的细节见下列图。

```text
title 1. Periodic Cycle Overview

participant Scheduler
participant RadarWorker
participant Reviewer

Scheduler->RadarWorker: run_periodic_cycle()
note right of RadarWorker: Single-instance worker\nThe outer cycle orchestrates stages explicitly:\n- A stage never drives the next stage by itself\n- A stage starts only after the previous stage finishes

RadarWorker->RadarWorker: Stage 1. Collect source items
note right of RadarWorker: Source adapters may run concurrently inside this stage\nAll fetched raw items are inserted or skipped before stage 2 starts

RadarWorker->RadarWorker: Stage 2. Create Recent Policy Updates
note right of RadarWorker: Each raw item is evaluated for the Radar feed\nRelevant items become policy updates; irrelevant items are discarded

RadarWorker->RadarWorker: Stage 3. Create policy impacts for review
note right of RadarWorker: Each policy update is evaluated into structured impact data\nso a reviewer can validate what the policy affects

RadarWorker-->Reviewer: policy impact ready for review\nvia webhook outbox + Stage 6
note right of Reviewer: Human approval is required\nbefore user actions can be created\nApproval only changes review status

RadarWorker->RadarWorker: Stage 4. Create user actions
note right of RadarWorker: Only approved policy impacts move forward\nActions are calculated separately for each target user

RadarWorker->RadarWorker: Stage 5. Send action notification emails
note right of RadarWorker: New user actions create email deliveries\nEmail sending happens after actions are committed

RadarWorker->RadarWorker: Stage 6. Send operational webhooks
note right of RadarWorker: Review-ready and attempt-exhausted events are sent to Lark\nWebhook sending is driven by status and attempt_count

note right of RadarWorker: Cycle summary:\n- Every cycle is state-driven\n- Each stage picks up newly eligible records\nand unfinished or failed records within retry limits

RadarWorker-->Scheduler: cycle finished
```

## 2. Stage 1: Collect source items

这张图描述 worker 如何按配置收集外部 source items，并把它们写成 raw source items。

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
      note right of SharedDB: Existing raw item is skipped\nand not updated
    end
  end
end

RadarWorker->RadarWorker: Stage 1 completed
```

## 3. Stage 2: Create Recent Policy Updates

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
        RadarWorker->SharedDB: insert webhook event if absent\nattempt_exhausted for raw_source_item_id\nentity_type = policy_update
      end
      RadarWorker->SharedDB: commit
      RadarWorker->RadarWorker: stop processing this raw item
    else PDF parsed
      PDFDownloader-->RadarWorker: parsed attachment context
    end
  end

  RadarWorker->LLM: filter + generate briefing\nsource_content + attachment context
  alt transient LLM failure
    LLM->LLM: retry with backoff\nup to 3 RPC attempts
  end
  LLM-->RadarWorker: ingest decision, briefing, and policy update fields

  alt invalid output or processing failed
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_update_status = failed\nincrement policy_update_attempt_count
    opt new policy_update_attempt_count reached 3
      RadarWorker->SharedDB: insert webhook event if absent\nattempt_exhausted for raw_source_item_id\nentity_type = policy_update
    end
    RadarWorker->SharedDB: commit
    RadarWorker->RadarWorker: stop processing this raw item
  else should_ingest = false
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_update_status = discarded\nincrement policy_update_attempt_count
    RadarWorker->SharedDB: commit
  else should_ingest = true
    RadarWorker->SharedDB: begin transaction
    RadarWorker->SharedDB: insert radar_policy_updates\nwrite source snapshot, briefing, and source_content\npolicy_extract_status = pending\npolicy_review_status = confirm_needed\naction_calculate_status = pending
    RadarWorker->SharedDB: set policy_update_status = ingested\nincrement policy_update_attempt_count
    RadarWorker->SharedDB: commit
  end
end

RadarWorker->RadarWorker: Stage 2 completed
```

## 4. Stage 3: Create policy impacts for review

这张图描述 policy update 如何触发黑盒抽取 policy impact。

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
    RadarWorker->SharedDB: insert webhook event if absent\npolicy_impact_ready_for_review for policy_update_id\nentity_type = policy_impact
    RadarWorker->SharedDB: commit
  else extract failed
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set policy_extract_status = failed\nincrement policy_extract_attempt_count
    opt new policy_extract_attempt_count reached 3
      RadarWorker->SharedDB: insert webhook event if absent\nattempt_exhausted for policy_update_id
    end
    RadarWorker->SharedDB: commit
  end
end

RadarWorker->RadarWorker: Stage 3 completed
```

## 5. Stage 4: Create user actions

这张图描述 approved policy impact 如何为目标用户生成 user actions。按 target user 显式循环；只有所有目标用户都计算成功后，才会统一提交 actions 和 email deliveries。

```text
title 5. Stage 4 - Create User Actions

participant Scheduler
participant RadarWorker
participant SharedDB
participant CMS
participant PolicyImpactBlackBox

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 4. Create user actions
note right of RadarWorker: create_user_actions() runs only after approval\nAll target users must be calculated before committing

RadarWorker->SharedDB: select policy updates with approved policy impacts\naction_calculate_status in pending/failed\naction_calculate_attempt_count < 3

loop each selected policy update
  RadarWorker->CMS: load target users
  CMS-->RadarWorker: user_ids

  loop each target user
    RadarWorker->PolicyImpactBlackBox: calculate_user_actions(policy_update_id, user_id)
    PolicyImpactBlackBox->SharedDB: read approved policy impact\nand user product/tariff data
    PolicyImpactBlackBox->PolicyImpactBlackBox: match affected products and derive suggested actions

    alt calculation failed
      PolicyImpactBlackBox-->RadarWorker: failure
      RadarWorker->RadarWorker: stop looping target users
    else candidates empty
      PolicyImpactBlackBox-->RadarWorker: no actions for this user
      RadarWorker->RadarWorker: keep no record for this user
    else candidates non-empty
      PolicyImpactBlackBox-->RadarWorker: affected products and suggested actions
      RadarWorker->RadarWorker: accumulate candidates in memory
    end
  end

  alt any target user calculation failed
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: set action_calculate_status = failed\nincrement action_calculate_attempt_count
    opt new action_calculate_attempt_count reached 3
      RadarWorker->SharedDB: insert webhook event if absent\nattempt_exhausted for policy_update_id
    end
    RadarWorker->SharedDB: commit
    RadarWorker->RadarWorker: stop processing this policy update
  else all target users calculated successfully
    RadarWorker->SharedDB: begin transaction
    RadarWorker->SharedDB: insert radar_user_actions\nfor accumulated candidates
    note right of SharedDB: affected_products and action_items\nare stored as JSONB on radar_user_actions
    RadarWorker->SharedDB: insert radar_email_deliveries\nwith email payload snapshots for active recipients
    RadarWorker->SharedDB: set action_calculate_status = succeeded\nincrement action_calculate_attempt_count
    RadarWorker->SharedDB: commit
  end
end

RadarWorker->RadarWorker: Stage 4 completed
```

## 6. Stage 5: Send action notification emails

这张图描述 action notification email 的发送。邮件是外部副作用，Email Provider 调用不在数据库事务内。

```text
title 6. Stage 5 - Send Action Notification Emails

participant Scheduler
participant RadarWorker
participant SharedDB
participant EmailService
participant EmailProvider

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 5. Send action notification emails
note right of RadarWorker: send_action_notifications() loads a batch of deliveries\nEach delivery is handled serially and checks latest recipient status
note right of RadarWorker: Recipient checks are best-effort\nA same-moment unsubscribe race may still send once

RadarWorker->SharedDB: select email deliveries\nstatus in pending/failed\nattempt_count < 3\nlimit N

alt no deliveries selected
    RadarWorker->RadarWorker: stop email stage
else deliveries selected
  loop each selected delivery
    RadarWorker->SharedDB: begin short transaction
    RadarWorker->SharedDB: load recipient

    alt recipient is not active
      RadarWorker->SharedDB: set status = skipped
      RadarWorker->SharedDB: commit
    else recipient is active
      RadarWorker->SharedDB: commit

      RadarWorker->EmailService: send email with delivery payload\nand strict timeout
      EmailService->EmailProvider: send email
      alt transient provider failure
        EmailService->EmailService: retry with backoff\nup to 3 RPC attempts
      end

      alt provider accepted
        EmailProvider-->EmailService: accepted
        EmailService-->RadarWorker: accepted
        RadarWorker->SharedDB: begin short transaction
        RadarWorker->SharedDB: set status = sent\nincrement attempt_count\nset last_attempt_at and sent_at
        RadarWorker->SharedDB: commit
      else provider failed or timed out
        EmailProvider-->EmailService: failure
        EmailService-->RadarWorker: failure
        RadarWorker->SharedDB: begin short transaction
        RadarWorker->SharedDB: set status = failed\nincrement attempt_count\nset last_attempt_at
        opt new attempt_count reached 3
          RadarWorker->SharedDB: insert webhook event if absent\nattempt_exhausted for delivery_id
        end
        RadarWorker->SharedDB: commit
      end
    end
  end
end

note right of RadarWorker: If provider accepted but process crashes before marking sent,\na later retry may send a duplicate.
```

## 7. Stage 6: Send operational webhooks

这张图描述 Lark operational webhook 的发送。Webhook event 是历史运营事件，不是实时业务状态投影；Stage 6 只按 outbox 状态发送事件。Lark 调用不在数据库事务内。

```text
title 7. Stage 6 - Send Operational Webhooks

participant Scheduler
participant RadarWorker
participant SharedDB
participant WebhookService
participant LarkTeam

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 6. Send operational webhooks
note right of RadarWorker: send_operational_webhooks() loads a batch of events\nEach event is handled serially with durable retry

RadarWorker->SharedDB: select webhook events\nstatus in pending/failed\nattempt_count < 3\nlimit N

alt no events selected
  RadarWorker->RadarWorker: stop webhook stage
else events selected
  loop each selected event
    RadarWorker->WebhookService: send webhook event with strict timeout
    WebhookService->LarkTeam: send Lark webhook
    alt transient webhook failure
      WebhookService->WebhookService: retry with backoff\nup to 3 RPC attempts
    end

    alt webhook accepted
      LarkTeam-->WebhookService: accepted
      WebhookService-->RadarWorker: accepted
      RadarWorker->SharedDB: begin short transaction
      RadarWorker->SharedDB: set status = sent\nincrement attempt_count\nset last_attempt_at and sent_at
      RadarWorker->SharedDB: commit
    else webhook failed or timed out
      LarkTeam-->WebhookService: failure
      WebhookService-->RadarWorker: failure
      RadarWorker->SharedDB: begin short transaction
      RadarWorker->SharedDB: set status = failed\nincrement attempt_count\nset last_attempt_at
      RadarWorker->SharedDB: commit
    end
  end
end
```

## 8. Policy Impact Review Flow

这张图描述 reviewer 如何查看、编辑、保存、approve policy impact。Approve 只推进审核状态；user actions 和邮件由周期任务处理。

```text
title 8. Policy Impact Review Flow

actor Reviewer
participant ReviewUI
participant ClassificationBackend
participant SharedDB
participant PolicyImpactBlackBox

Reviewer->ReviewUI: open review page
ReviewUI->ClassificationBackend: GET /api/compliance-radar/review/policy-impacts/{policy_update_id}
note right of ClassificationBackend: All review endpoints require existing admin/internal auth
ClassificationBackend->SharedDB: load policy update
SharedDB-->ClassificationBackend: policy update
ClassificationBackend->PolicyImpactBlackBox: get_policy_impact(policy_update_id)
PolicyImpactBlackBox-->ClassificationBackend: policy_impact object
ClassificationBackend-->ReviewUI: policy update + policy_impact

opt reviewer edits policy impact
  Reviewer->ReviewUI: edit and save policy impact
  ReviewUI->ClassificationBackend: PUT /api/compliance-radar/review/policy-impacts/{policy_update_id}
  ClassificationBackend->SharedDB: verify policy_extract_status = succeeded\nand policy_review_status = confirm_needed
  ClassificationBackend->PolicyImpactBlackBox: validate_policy_impact(policy_update_id, policy_impact)
  PolicyImpactBlackBox-->ClassificationBackend: {success, message}

  alt validation success
    ClassificationBackend->PolicyImpactBlackBox: save_policy_impact(policy_update_id, policy_impact)
    ClassificationBackend-->ReviewUI: saved
  else validation failed
    ClassificationBackend-->ReviewUI: 422 message
  end
end

alt reviewer approves
  ReviewUI->ClassificationBackend: POST /api/compliance-radar/review/policy-impacts/{policy_update_id}/approve
  ClassificationBackend->SharedDB: verify policy_extract_status = succeeded\nand policy_review_status = confirm_needed
  ClassificationBackend->PolicyImpactBlackBox: validate_policy_impact(policy_update_id)
  PolicyImpactBlackBox-->ClassificationBackend: {success, message}

  alt validation success
    ClassificationBackend->SharedDB: set policy_review_status = approved
    ClassificationBackend-->ReviewUI: approved
    note right of ClassificationBackend: Approved policy impacts are picked up\nby a later periodic cycle
  else validation failed
    ClassificationBackend-->ReviewUI: 422 message
  end
end
```

## 9. User action usage and execution entry

这张图描述用户查看 actions、完成或取消完成 action item，以及点击 Go 进入 classification/sandbox 执行动作。

```text
title 9. User Action Usage and Execution Entry

actor User
participant Frontend
participant ClassificationBackend
participant SharedDB
participant ClassificationProduct
participant SandboxCalculator

User->Frontend: open Compliance Radar Actions
Frontend->ClassificationBackend: GET /api/compliance-radar/actions\nstatus, policy_update_id, page, page_size
ClassificationBackend->SharedDB: query current user's actions\nwith affected products and action items
SharedDB-->ClassificationBackend: user actions
ClassificationBackend-->Frontend: user actions
Frontend-->User: render actions

User->Frontend: complete or uncomplete an action item
Frontend->ClassificationBackend: PATCH /api/compliance-radar/actions/{action_id}/items/{action_type}/completion\n{completed: true/false}
ClassificationBackend->SharedDB: begin transaction
ClassificationBackend->SharedDB: verify action belongs to current user
ClassificationBackend->SharedDB: update action_items JSON status\nfor the selected action_type
ClassificationBackend->SharedDB: recompute action status

alt all items completed
  ClassificationBackend->SharedDB: set action status = completed\nset completed_at / completed_by
else any item still action_needed
  ClassificationBackend->SharedDB: set action status = action_needed\nclear completed_at / completed_by
end

ClassificationBackend->SharedDB: commit
ClassificationBackend-->Frontend: updated action
note right of ClassificationBackend: Completion changes do not send emails

User->Frontend: click Go on reclassify_product
Frontend->Frontend: filter affected products\nwhere suggested_actions contains reclassify_product
Frontend->ClassificationProduct: navigate with action context\npolicy_update_id, action_type, product_uids
note right of ClassificationProduct: Actual reclassification is owned by classification product\nRadar does not execute it

User->Frontend: click Go on recalculate_tariff
Frontend->Frontend: filter affected products\nwhere suggested_actions contains recalculate_tariff
Frontend->SandboxCalculator: navigate with action context\npolicy_update_id, action_type, product_uids
note right of SandboxCalculator: Actual recalculation is owned by sandbox/calculator\nRadar does not execute it
```

## 10. Notification Recipients and Unsubscribe

这张图描述用户管理通知邮箱、系统为新 actions 创建 email deliveries，以及收件人 unsubscribe。

```text
title 10. Notification Recipients and Unsubscribe

actor User
actor Recipient
participant Frontend
participant ClassificationBackend
participant SharedDB
participant RadarWorker
participant EmailService
participant EmailProvider

User->Frontend: add notification email
Frontend->ClassificationBackend: POST /api/compliance-radar/notification-recipients\n{email}
ClassificationBackend->ClassificationBackend: validate email format
ClassificationBackend->SharedDB: check existing recipient\nand active recipient count

alt active count >= 5
  ClassificationBackend-->Frontend: 409 limit exceeded
else email already active
  ClassificationBackend-->Frontend: 409 duplicate
else email already unsubscribed
  ClassificationBackend-->Frontend: 409 unsubscribed
else email exists as deleted
  ClassificationBackend->SharedDB: reactivate as active\nrefresh unsubscribe_token
  ClassificationBackend-->Frontend: recipient
else new email
  ClassificationBackend->SharedDB: insert recipient as active\ncreate unsubscribe_token
  ClassificationBackend-->Frontend: recipient
end

User->Frontend: delete notification email
Frontend->ClassificationBackend: DELETE /api/compliance-radar/notification-recipients/{recipient_id}
ClassificationBackend->SharedDB: verify recipient belongs to current user
ClassificationBackend->SharedDB: set status = deleted
ClassificationBackend-->Frontend: success

RadarWorker->SharedDB: while creating user actions\nload active recipients
RadarWorker->SharedDB: insert email deliveries in the same transaction\none per user_action_id + recipient_id
RadarWorker->RadarWorker: later Stage 5 sends pending deliveries
RadarWorker->EmailService: send action notification with unsubscribe link
EmailService->EmailProvider: send email
EmailProvider-->Recipient: impact action email

Recipient->Frontend: open unsubscribe link
note right of Frontend: /compliance-radar/unsubscribe?token=...
Frontend->ClassificationBackend: GET /api/compliance-radar/unsubscribe/{token}
ClassificationBackend->SharedDB: find recipient by token

alt token not found
  ClassificationBackend-->Frontend: 404
else token found
  ClassificationBackend->SharedDB: set status = unsubscribed
  ClassificationBackend-->Frontend: success or already handled
end

note right of RadarWorker: Existing sent deliveries remain historical records\nFuture deliveries only use active recipients\nUnsubscribed/deleted pending deliveries are marked skipped by Stage 5
```

## 11. Action execution boundary

Radar 只负责展示 action、保存完成状态，并提供跳转所需的 policy/action context。`reclassify_product` 和 `recalculate_tariff` 的实际执行流程分别由 classification 和 sandbox/calculator 产品承载，不在 Radar worker 主流程内建模。
