-- Run this in the Supabase SQL editor for the shared project
-- (same project as nurse-ot-line-main)

-- Shift schedule table
CREATE TABLE IF NOT EXISTS schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES nurses(id) ON DELETE CASCADE,
    schedule_date DATE NOT NULL,
    shift_type VARCHAR(20) NOT NULL,  -- 7-3 / 9-5 / 10-6 / 12-8 / 3-11 / 11-7 / 其他 / 休
    area VARCHAR(50),
    source_version VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active | swapped | cancelled
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS schedules_user_date_version
    ON schedules(user_id, schedule_date, source_version);

CREATE INDEX IF NOT EXISTS schedules_date_idx ON schedules(schedule_date);
CREATE INDEX IF NOT EXISTS schedules_user_idx ON schedules(user_id);

-- OT priority list per day
CREATE TABLE IF NOT EXISTS ot_priority (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    priority_date DATE NOT NULL,
    user_id UUID NOT NULL REFERENCES nurses(id) ON DELETE CASCADE,
    priority_order INTEGER NOT NULL,
    source_version VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active | swapped
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ot_priority_date_order_version
    ON ot_priority(priority_date, priority_order, source_version);

CREATE INDEX IF NOT EXISTS ot_priority_date_idx ON ot_priority(priority_date);
CREATE INDEX IF NOT EXISTS ot_priority_user_idx ON ot_priority(user_id);

-- Swap requests (all types): audit trail for all swap actions
CREATE TABLE IF NOT EXISTS swap_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_type VARCHAR(20) NOT NULL,      -- shift | ot_priority | manager_direct
    status VARCHAR(30) NOT NULL DEFAULT 'submitted',
    -- submitted | pending_peer | pending_admin | approved | rejected
    -- cancelled | conflict_rejected | peer_rejected

    requester_id UUID REFERENCES nurses(id),   -- null for manager_direct
    schedule_id UUID REFERENCES schedules(id),
    ot_priority_id UUID REFERENCES ot_priority(id),

    target_user_id UUID REFERENCES nurses(id),
    target_schedule_id UUID REFERENCES schedules(id),
    target_ot_priority_id UUID REFERENCES ot_priority(id),

    reason TEXT,

    peer_response VARCHAR(20),          -- accepted | rejected
    peer_responded_at TIMESTAMPTZ,

    admin_decision VARCHAR(20),         -- approved | rejected
    admin_comment TEXT,
    admin_decided_at TIMESTAMPTZ,

    performed_by UUID REFERENCES nurses(id),   -- manager who did direct swap

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS swap_requests_requester_idx ON swap_requests(requester_id);
CREATE INDEX IF NOT EXISTS swap_requests_target_idx ON swap_requests(target_user_id);
CREATE INDEX IF NOT EXISTS swap_requests_status_idx ON swap_requests(status);
CREATE INDEX IF NOT EXISTS swap_requests_schedule_idx ON swap_requests(schedule_id);
CREATE INDEX IF NOT EXISTS swap_requests_target_schedule_idx ON swap_requests(target_schedule_id);
CREATE INDEX IF NOT EXISTS swap_requests_ot_idx ON swap_requests(ot_priority_id);
CREATE INDEX IF NOT EXISTS swap_requests_target_ot_idx ON swap_requests(target_ot_priority_id);
