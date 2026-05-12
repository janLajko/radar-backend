BEGIN;

CREATE OR REPLACE FUNCTION radar_set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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

CREATE TRIGGER trg_radar_raw_source_items_updated_at
BEFORE UPDATE ON radar_raw_source_items
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

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

CREATE TRIGGER trg_radar_policy_updates_updated_at
BEFORE UPDATE ON radar_policy_updates
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE TABLE radar_user_actions (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL,
  policy_update_id bigint NOT NULL,
  affected_products jsonb NOT NULL DEFAULT '[]'::jsonb,
  action_items jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'action_needed',
  completed_at timestamptz,
  completed_by bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_user_actions_user_policy
    UNIQUE (user_id, policy_update_id),
  CONSTRAINT chk_radar_user_actions_status
    CHECK (status IN ('action_needed', 'completed')),
  CONSTRAINT chk_radar_user_actions_affected_products_array
    CHECK (jsonb_typeof(affected_products) = 'array'),
  CONSTRAINT chk_radar_user_actions_action_items_array
    CHECK (jsonb_typeof(action_items) = 'array')
);

CREATE TRIGGER trg_radar_user_actions_updated_at
BEFORE UPDATE ON radar_user_actions
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE TABLE radar_notification_recipients (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL,
  email text NOT NULL,
  unsubscribe_token text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_notification_recipients_unsubscribe_token
    UNIQUE (unsubscribe_token),
  CONSTRAINT chk_radar_notification_recipients_status
    CHECK (status IN ('active', 'unsubscribed', 'deleted'))
);

CREATE TRIGGER trg_radar_notification_recipients_updated_at
BEFORE UPDATE ON radar_notification_recipients
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE TABLE radar_email_deliveries (
  id bigserial PRIMARY KEY,
  user_action_id bigint NOT NULL,
  recipient_id bigint NOT NULL,
  recipient_email text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_email_deliveries_action_recipient
    UNIQUE (user_action_id, recipient_id),
  CONSTRAINT chk_radar_email_deliveries_status
    CHECK (status IN ('pending', 'sent', 'failed')),
  CONSTRAINT chk_radar_email_deliveries_attempt_count
    CHECK (attempt_count >= 0)
);

CREATE TRIGGER trg_radar_email_deliveries_updated_at
BEFORE UPDATE ON radar_email_deliveries
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE TABLE radar_webhook_events (
  id bigserial PRIMARY KEY,
  event_type text NOT NULL,
  entity_type text NOT NULL,
  entity_id bigint NOT NULL,
  channel text NOT NULL DEFAULT 'lark_team',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_webhook_events_event_entity_channel
    UNIQUE (event_type, entity_type, entity_id, channel),
  CONSTRAINT chk_radar_webhook_events_event_type
    CHECK (event_type IN ('policy_impact_ready_for_review', 'attempt_exhausted')),
  CONSTRAINT chk_radar_webhook_events_entity_type
    CHECK (entity_type IN ('raw_policy_update', 'policy_extract', 'action_calculate', 'email_delivery')),
  CONSTRAINT chk_radar_webhook_events_status
    CHECK (status IN ('pending', 'sent', 'failed')),
  CONSTRAINT chk_radar_webhook_events_payload_object
    CHECK (jsonb_typeof(payload) = 'object'),
  CONSTRAINT chk_radar_webhook_events_attempt_count
    CHECK (attempt_count >= 0)
);

CREATE TRIGGER trg_radar_webhook_events_updated_at
BEFORE UPDATE ON radar_webhook_events
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE INDEX idx_radar_raw_source_items_processing
ON radar_raw_source_items (policy_update_status, policy_update_attempt_count, created_at);

CREATE INDEX idx_radar_policy_updates_list
ON radar_policy_updates (published_at DESC NULLS LAST, created_at DESC);

CREATE INDEX idx_radar_policy_updates_source_list
ON radar_policy_updates (source_key, published_at DESC NULLS LAST, created_at DESC);

CREATE INDEX idx_radar_policy_updates_extract_work
ON radar_policy_updates (policy_extract_status, policy_extract_attempt_count, created_at);

CREATE INDEX idx_radar_policy_updates_action_work
ON radar_policy_updates (policy_review_status, action_calculate_status, action_calculate_attempt_count, created_at);

CREATE INDEX idx_radar_user_actions_user_status
ON radar_user_actions (user_id, status, created_at DESC);

CREATE UNIQUE INDEX uq_radar_notification_recipients_user_email
ON radar_notification_recipients (user_id, lower(email))
WHERE status IN ('active', 'unsubscribed');

CREATE INDEX idx_radar_notification_recipients_user_status
ON radar_notification_recipients (user_id, status);

CREATE INDEX idx_radar_email_deliveries_send_work
ON radar_email_deliveries (created_at)
WHERE status IN ('pending', 'failed') AND attempt_count < 3;

CREATE INDEX idx_radar_webhook_events_dispatch_work
ON radar_webhook_events (status, attempt_count, created_at);

COMMIT;
