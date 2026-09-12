-- 0003 — the charge log becomes a RESERVATION log, and a vehicle is registered
-- at one agreement per garage.
--
-- Run as the database OWNER, after 0002. The application never connects as
-- this role. 0002 is not edited: what it created stands, and this migration
-- alters it.
--
-- THE INVOICE ROW IS THE LOCK, AND THAT DEPENDS ON A GRANT 0002 KEEPS. Every
-- event that changes an invoice's money -- a payment, a reversal, an owner's
-- adjustment, both halves of a charge -- now runs inside a transaction that
-- first takes `SELECT ... FROM invoices WHERE id = %s FOR UPDATE`. A row lock
-- taken with FOR UPDATE needs the UPDATE privilege on the table, and 0002 kept
-- UPDATE on `invoices` for the one derived column, `paid_at`. ⛔ THAT GRANT IS
-- NOT TO BE TIGHTENED: revoke UPDATE on invoices and every money event in the
-- module is refused by the database before it can serialise. See
-- src/monthly_billing/store/postgres.py, lock_invoice.
--
-- No schema statement here creates the lock; it is a property of the code,
-- read from the source by the contract generator and proven by a control that
-- removes it and requires two writers to collide again.

BEGIN;

-- ---------------------------------------------------------------------------
-- charge_attempts — from a log of what the processor SAID to a log of what was
-- ASKED and what was said, as two rows.
--
-- The outside pass showed two crash windows in the old shape: a processor that
-- succeeded before any row was written left nothing, and a restart charged
-- again; a SUCCESS row committed before its payment reset the retry count, and
-- a restart charged again. Both because the row was written AFTER the
-- processor was called. Now:
--
--   kind = 'attempt'   -- the RESERVATION, written under the invoice lock BEFORE
--                         the processor is called. Carries the attempt id the
--                         processor is handed as its idempotency key, and the
--                         amount and currency reserved -- the balance at that
--                         instant, which is what the processor was asked for
--                         whatever the balance is by the time the outcome lands.
--                         `outcome` is NULL: the attempt is PENDING.
--   kind = 'outcome'   -- what the processor said, as a second row naming the
--                         attempt: success, decline or error. On success the
--                         card payment is written in the SAME transaction.
--   kind = 'payment_method_changed' -- as before.
--
-- A pending attempt with no outcome row is never charged past: the next charge
-- is refused by name until an operator records what the processor says. The
-- log stays append-only by grant; nothing here is updated.
--
-- NO DATA MIGRATION, ASSERTED RATHER THAN ASSUMED, as 0002 did: an existing
-- `attempt` row has no attempt_id and no amount, and the CHECKs below would
-- refuse it. The module had never been deployed when this was written; if that
-- premise no longer holds where this runs, stop and write the data migration.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  populated integer;
BEGIN
  SELECT count(*) INTO populated FROM charge_attempts;
  IF populated > 0 THEN
    RAISE EXCEPTION
      'charge_attempts holds % row(s). This migration reshapes the log and carries '
      'no data, because the module had never been deployed when it was written. '
      'That premise no longer holds here: stop, and write the data migration.',
      populated;
  END IF;
END
$$;

ALTER TABLE charge_attempts
  ADD COLUMN attempt_id   uuid,
  ADD COLUMN amount_minor bigint,
  ADD COLUMN currency     char(3);

ALTER TABLE charge_attempts
  DROP CONSTRAINT charge_attempts_kind_check,
  DROP CONSTRAINT charge_attempts_outcome_iff_attempt;

ALTER TABLE charge_attempts
  ADD CONSTRAINT charge_attempts_kind_check
    CHECK (kind IN ('attempt', 'outcome', 'payment_method_changed')),
  -- The three shapes, each all-or-nothing. A reservation names its attempt and
  -- carries the money and no outcome; an outcome names its attempt, says how it
  -- went, and carries no money of its own (the reservation already did); a
  -- method change carries none of it.
  ADD CONSTRAINT charge_attempts_shape_by_kind CHECK (
    (kind = 'attempt'
       AND outcome IS NULL AND attempt_id IS NOT NULL
       AND amount_minor IS NOT NULL AND amount_minor > 0 AND currency IS NOT NULL) OR
    (kind = 'outcome'
       AND outcome IS NOT NULL AND attempt_id IS NOT NULL
       AND amount_minor IS NULL AND currency IS NULL) OR
    (kind = 'payment_method_changed'
       AND outcome IS NULL AND attempt_id IS NULL
       AND amount_minor IS NULL AND currency IS NULL)
  );

-- One reservation and at most one outcome per attempt id. The module refuses
-- a second outcome by name before this fires; this is the backstop for a raw
-- insert, and for two resolvers racing.
CREATE UNIQUE INDEX charge_attempts_one_row_per_kind_per_attempt
  ON charge_attempts (tenant_id, attempt_id, kind)
  WHERE attempt_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- vehicle_registrations — ONE CAR, ONE AGREEMENT PER GARAGE. His ruling.
--
-- 0001's UNIQUE on agreement_vehicles is per AGREEMENT, so the same plate could
-- sit on two agreements at one garage and the highest id decided coverage. This
-- table is the garage-wide fact: which agreement a vehicle identity is
-- registered to, right now. It is CURRENT STATE, not history -- the application
-- role keeps UPDATE and DELETE, and a version that drops the vehicle or a
-- cancellation that has taken effect removes or replaces the row.
--
-- The module refuses a second registration by name before this constraint has
-- to (REFUSAL_VEHICLE_ALREADY_REGISTERED, naming the other agreement); the
-- UNIQUE is the backstop for a raw insert and for two registrations racing.
--
-- `agreement_external_id` is the agreement's IDENTITY across versions, not a
-- version row: a price change stores a new version and the registration does
-- not move. It is deliberately not a foreign key to `agreements(id)` -- that
-- would pin it to one version row.
--
-- A registration whose latest version is cancelled stays until
-- `cancelled_effective_day` -- the car is still covered until then -- and is
-- RELEASED by the next registration attempt on or after that day (garage-local).
--
-- Current state, not money: G12 covers this table from the catalogue (tenant
-- column, ENABLE, FORCE, policy). There is deliberately NO composite-tenant
-- foreign key here and no G28-style control for one -- written down so nobody
-- adds one later believing it was an oversight. G28 is about money-history rows
-- pointing at money-history rows; this table points at a garage.
-- ---------------------------------------------------------------------------
CREATE TABLE vehicle_registrations (
  id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  garage_id              uuid        NOT NULL REFERENCES garages(id) ON DELETE CASCADE,
  identity_normalised    text        NOT NULL CHECK (length(btrim(identity_normalised)) > 0),
  agreement_external_id  text        NOT NULL CHECK (length(btrim(agreement_external_id)) > 0),
  registered_at          timestamptz NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT vehicle_registrations_one_agreement_per_garage
    UNIQUE (tenant_id, garage_id, identity_normalised)
);

CREATE INDEX vehicle_registrations_tenant_id_idx ON vehicle_registrations (tenant_id);
CREATE INDEX vehicle_registrations_agreement_idx
  ON vehicle_registrations (tenant_id, garage_id, agreement_external_id);
ALTER TABLE vehicle_registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE vehicle_registrations FORCE  ROW LEVEL SECURITY;
CREATE POLICY vehicle_registrations_tenant_isolation ON vehicle_registrations
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON vehicle_registrations TO monthly_billing_app;

COMMIT;
