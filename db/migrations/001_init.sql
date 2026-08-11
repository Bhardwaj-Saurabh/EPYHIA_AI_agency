-- 001_init.sql - faithful translation of DESIGN.md section 8.
-- Money: integer cents (customer-facing) / integer microdollars (cost tracking).
-- The unique constraints here are the idempotency guarantees - do not relax them.

CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  business_name TEXT NOT NULL,
  business_slug TEXT NOT NULL UNIQUE,
  business_email TEXT,
  business_phone TEXT,
  business_address TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE brand_document (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  version_number INTEGER NOT NULL,
  full_text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, version_number)
);

CREATE TABLE runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  original_brief TEXT NOT NULL,
  completed_brief TEXT,
  brief_hash TEXT NOT NULL,
  brand_document_id UUID REFERENCES brand_document(id),
  approved_budget_microdollars BIGINT NOT NULL,
  budget_approved_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'CREATED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
  -- total cost is derived by query from agent_calls + actions (DESIGN.md section 8)
);

CREATE TABLE onboarding_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  idempotency_key TEXT NOT NULL,
  run_id UUID NOT NULL REFERENCES runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  run_id UUID NOT NULL REFERENCES runs(id),
  task_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  output_ref TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, task_type)
);

CREATE TABLE agent_calls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES runs(id),
  task_id UUID REFERENCES tasks(id),
  agent_name TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model_tier TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  cached_input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_microdollars BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

-- The audit log (DESIGN.md section 4): one row per gated action.
CREATE TABLE actions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  run_id UUID REFERENCES runs(id),
  agent_name TEXT NOT NULL,
  action_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'TEST',
  approval_status TEXT NOT NULL,
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  provider_reference TEXT,
  provider_cost_microdollars BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  executed_at TIMESTAMPTZ,
  UNIQUE (tenant_id, action_type, idempotency_key)
);

CREATE TABLE marketing_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  run_id UUID NOT NULL REFERENCES runs(id),
  brand_document_id UUID NOT NULL REFERENCES brand_document(id),
  artifact_type TEXT NOT NULL CHECK (
    artifact_type IN ('LANDING_COPY','SOCIAL_POST','LAUNCH_EMAIL','VIDEO_STORYBOARD','VIDEO_LANDSCAPE','VIDEO_VERTICAL')
  ),
  sequence_number INTEGER NOT NULL DEFAULT 1,
  channel TEXT,
  text_content TEXT,
  r2_object_key TEXT,
  mime_type TEXT,
  self_review_status TEXT NOT NULL DEFAULT 'PENDING',
  grounding_check_status TEXT NOT NULL DEFAULT 'PENDING',
  review_feedback TEXT,
  approval_status TEXT NOT NULL DEFAULT 'PENDING',
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, artifact_type, sequence_number),
  -- text artifacts carry text; video artifacts carry a stored object (DESIGN.md section 8)
  CHECK (
    (artifact_type IN ('VIDEO_LANDSCAPE','VIDEO_VERTICAL') AND r2_object_key IS NOT NULL AND mime_type IS NOT NULL)
    OR
    (artifact_type NOT IN ('VIDEO_LANDSCAPE','VIDEO_VERTICAL') AND text_content IS NOT NULL AND text_content <> '')
  )
);

CREATE TABLE rental_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL,
  description TEXT,
  available_qty INTEGER NOT NULL CHECK (available_qty >= 0),
  day_rate INTEGER NOT NULL CHECK (day_rate >= 0) -- integer cents
);

CREATE TABLE customers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  normalized_email TEXT NOT NULL, -- lower(trim(email)), computed by application code
  UNIQUE (tenant_id, normalized_email)
);

CREATE TABLE reservations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  customer_id UUID NOT NULL REFERENCES customers(id),
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  total INTEGER NOT NULL CHECK (total >= 0), -- integer cents
  stripe_checkout_session_id TEXT,
  is_synthetic BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date >= start_date)
);

CREATE TABLE reservation_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reservation_id UUID NOT NULL REFERENCES reservations(id),
  rental_item_id UUID NOT NULL REFERENCES rental_items(id),
  qty INTEGER NOT NULL CHECK (qty > 0),
  day_rate INTEGER NOT NULL CHECK (day_rate >= 0) -- cents, captured at booking time
);

-- "One order, never two" is enforced HERE, not in application code.
CREATE TABLE orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  reservation_id UUID NOT NULL UNIQUE REFERENCES reservations(id),
  stripe_checkout_session_id TEXT NOT NULL UNIQUE,
  amount INTEGER NOT NULL CHECK (amount >= 0), -- integer cents
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  payment_timestamp TIMESTAMPTZ NOT NULL,
  is_synthetic BOOLEAN NOT NULL DEFAULT false
);

-- Stripe retries webhooks; this table is the dedupe.
CREATE TABLE webhook_events (
  stripe_event_id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE deployments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL UNIQUE REFERENCES tenants(id),
  cloudflare_project_name TEXT NOT NULL UNIQUE,
  live_url TEXT,
  last_action_id UUID REFERENCES actions(id),
  verified_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
