CREATE TABLE IF NOT EXISTS bed_transfers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ref text NOT NULL UNIQUE CHECK (ref ~ '^[A-Z0-9][A-Z0-9-]{4,39}$'),
    note text NOT NULL CHECK (length(btrim(note)) BETWEEN 3 AND 600),
    requested_by bigint NOT NULL REFERENCES staff,
    status text NOT NULL CHECK (status IN ('applied','voided')),
    void_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    CHECK ((status = 'applied' AND void_reason IS NULL AND applied_at IS NOT NULL) OR
           (status = 'voided' AND void_reason IS NOT NULL AND applied_at IS NULL))
);

CREATE TABLE IF NOT EXISTS bed_transfer_moves (
    transfer_id bigint NOT NULL REFERENCES bed_transfers,
    resident_id bigint NOT NULL REFERENCES residents,
    from_unit text NOT NULL,
    from_room text NOT NULL,
    to_unit text NOT NULL,
    to_room text NOT NULL,
    PRIMARY KEY (transfer_id, resident_id),
    CHECK (from_unit <> to_unit OR from_room <> to_room)
);
CREATE INDEX IF NOT EXISTS bed_transfer_move_resident ON bed_transfer_moves(resident_id, transfer_id DESC);

CREATE TABLE IF NOT EXISTS bed_transfer_kept_responses (
    transfer_id bigint NOT NULL REFERENCES bed_transfers,
    response_id bigint NOT NULL REFERENCES care_responses,
    resident_id bigint NOT NULL REFERENCES residents,
    assignee bigint NOT NULL REFERENCES staff,
    PRIMARY KEY (transfer_id, response_id)
);

CREATE TABLE IF NOT EXISTS bed_transfer_redispatches (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_id bigint NOT NULL REFERENCES bed_transfers,
    response_id bigint NOT NULL UNIQUE REFERENCES care_responses,
    alert_id bigint NOT NULL REFERENCES alerts,
    alert_revision int NOT NULL CHECK (alert_revision > 0),
    resident_id bigint NOT NULL REFERENCES residents,
    assignee bigint NOT NULL REFERENCES staff,
    priority smallint NOT NULL CHECK (priority BETWEEN 1 AND 3),
    from_unit text NOT NULL,
    to_unit text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','dispatched')),
    new_response_id bigint REFERENCES care_responses,
    created_at timestamptz NOT NULL DEFAULT now(),
    dispatched_at timestamptz,
    CHECK ((status = 'pending' AND new_response_id IS NULL AND dispatched_at IS NULL) OR
           (status = 'dispatched' AND new_response_id IS NOT NULL AND dispatched_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_redispatch_per_alert
    ON bed_transfer_redispatches(alert_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS redispatch_transfer_history
    ON bed_transfer_redispatches(transfer_id, id);
