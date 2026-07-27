-- Wholesale Agent Database Schema v3
-- Adds: attorney-clearance gates on orgs, contract_documents table
-- Run this in Supabase SQL Editor (Project > SQL Editor > New Query)

-- ============================================================
-- ORGS: multi-tenant foundation. Even running solo, everything
-- is scoped to an org_id so this can be licensed/sold later
-- without a schema rewrite.
-- ============================================================
CREATE TABLE orgs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    contact_phone TEXT,                       -- for SMS brief delivery
    brief_send_hour_local INTEGER DEFAULT 7,  -- hour of day (24h, org's local tz) to send daily brief
    timezone TEXT DEFAULT 'America/New_York',
    plan_tier TEXT DEFAULT 'owner',           -- 'owner' (you) vs 'licensed' (future customers)
    attorney_cleared_general_wholesaling BOOLEAN DEFAULT FALSE,  -- set TRUE only after a real
                                                -- FL attorney confirms YOU specifically qualify
                                                -- for the principal-buyer exemption. Blocks all
                                                -- offer drafting AND contract generation until true.
    attorney_cleared_foreclosure BOOLEAN DEFAULT FALSE,  -- set TRUE only after a real FL attorney
                                                -- confirms your specific letter/contract structure
                                                -- re: 501.1377 "foreclosure-rescue transaction."
                                                -- Blocks offer drafting on ANY lis-pendens lead
                                                -- until true, regardless of leaseback structure.
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Insert your own org as the first row once this runs:
-- INSERT INTO orgs (name, contact_email, contact_phone) VALUES ('Nick - Primary', 'you@email.com', '+1561XXXXXXX');

-- ============================================================
-- LEADS
-- ============================================================
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id),
    source TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT,
    state TEXT,
    zip TEXT,
    county TEXT,
    owner_name TEXT,
    owner_phone TEXT,
    owner_email TEXT,
    owner_mailing_address TEXT,
    estimated_value NUMERIC,
    equity_estimate NUMERIC,
    distress_signals JSONB DEFAULT '[]',
    is_lis_pendens_filed BOOLEAN DEFAULT FALSE,   -- TRUE = active foreclosure notice recorded
    includes_leaseback_or_repurchase BOOLEAN DEFAULT FALSE,  -- TRUE only if THIS deal's
                                                    -- structure gives the homeowner a leaseback
                                                    -- or repurchase option. Informational only --
                                                    -- the hard gate in offer_drafting.py blocks ANY
                                                    -- lis-pendens lead regardless of this flag.
    raw_payload JSONB,
    status TEXT DEFAULT 'new',
    last_activity_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE lead_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    score NUMERIC NOT NULL,
    reasoning TEXT,
    model_used TEXT DEFAULT 'claude-sonnet-4-6',
    scored_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE comps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    comp_address TEXT,
    sale_price NUMERIC,
    sale_date DATE,
    sqft NUMERIC,
    beds INTEGER,
    baths NUMERIC,
    distance_miles NUMERIC,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE deal_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    arv NUMERIC,
    estimated_repairs NUMERIC,
    target_assignment_fee NUMERIC DEFAULT 12000,
    mao NUMERIC,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE offers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    offer_amount NUMERIC NOT NULL,
    draft_letter TEXT NOT NULL,
    approval_status TEXT DEFAULT 'pending_review',
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    gmail_message_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE buyers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id),
    name TEXT NOT NULL,
    company TEXT,
    email TEXT,
    phone TEXT,
    buy_box JSONB,
    is_institutional BOOLEAN DEFAULT FALSE,
    closed_deals_count INTEGER DEFAULT 0,
    last_closed_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE deals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id),
    lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
    offer_id UUID REFERENCES offers(id),
    stage TEXT DEFAULT 'offer_sent',
    contract_price NUMERIC,
    emd_amount NUMERIC,
    inspection_deadline DATE,
    closing_deadline DATE,
    assigned_buyer_id UUID REFERENCES buyers(id),
    assignment_fee NUMERIC,
    closed_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE communications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id UUID REFERENCES leads(id),
    deal_id UUID REFERENCES deals(id),
    buyer_id UUID REFERENCES buyers(id),
    direction TEXT NOT NULL,
    channel TEXT DEFAULT 'email',
    subject TEXT,
    body TEXT,
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- DIRECTIVES: the "what do I need to do today" engine.
-- ============================================================
CREATE TABLE directives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id),
    deal_id UUID REFERENCES deals(id),
    lead_id UUID REFERENCES leads(id),
    directive_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE notification_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id),
    channel TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    directive_count INTEGER,
    sent_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- CONTRACT_DOCUMENTS: generated Purchase & Sale + Assignment
-- agreements. TEMPLATE-BASED, not LLM-generated -- a legal
-- document should have fixed, attorney-reviewed language with
-- fields filled in, not AI-variable phrasing per generation.
-- ============================================================
CREATE TABLE contract_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES orgs(id),
    deal_id UUID REFERENCES deals(id),
    document_type TEXT NOT NULL,       -- 'purchase_agreement', 'assignment_agreement'
    document_text TEXT NOT NULL,
    attorney_reviewed BOOLEAN DEFAULT FALSE,  -- set TRUE only after YOUR attorney has
                                        -- actually reviewed/approved this exact template
                                        -- once -- not per-document, the template itself
    generated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_leads_org_status ON leads(org_id, status);
CREATE INDEX idx_leads_zip ON leads(zip);
CREATE INDEX idx_deals_org_stage ON deals(org_id, stage);
CREATE INDEX idx_offers_approval_status ON offers(approval_status);
CREATE INDEX idx_directives_org_resolved ON directives(org_id, resolved);
CREATE INDEX idx_buyers_org ON buyers(org_id);
CREATE INDEX idx_contract_documents_org_type ON contract_documents(org_id, document_type, generated_at DESC);
