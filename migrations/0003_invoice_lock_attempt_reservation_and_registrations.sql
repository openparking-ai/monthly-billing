-- 0003 — the charge log becomes a RESERVATION log, a vehicle is registered at
-- one agreement per garage, and every garage reference is half of a composite
-- tenant key.
--
-- Run as the database OWNER, after 0002. The application never connects as
-- this role. 0001 and 0002 are not edited: what they created stands, and this
-- migration alters it.
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
-- ASKED and what was said, as separate rows.
--
-- The outside pass showed two crash windows in the old shape: a processor that
-- succeeded before any row was written left nothing, and a restart charged
-- again; a SUCCESS row committed before its payment reset the retry count, and
-- a restart charged again. Both because the row was written AFTER the
-- processor was called. Its second round showed the third window: an answer
-- the module never received -- the processor raised, or returned something the
-- instrument guard refused -- was written as a resolved `error` outcome, and
-- the next charge asked again under a FRESH key. Now:
--
--   kind = 'attempt'   -- the RESERVATION, written under the invoice lock BEFORE
--                         the processor is called. Carries the attempt id the
--                         processor is handed as its idempotency key, and the
--                         amount and currency reserved -- the balance at that
--                         instant, which is what the processor was asked for
--                         whatever the balance is by the time the outcome lands.
--                         `outcome` is NULL: the attempt is PENDING.
--   kind = 'unknown'   -- an ask of this attempt came back UNKNOWN: the module
--                         does not know what the processor did. `detail` says
--                         what the module saw (the exception, the guard's fixed
--                         sentence, or `withheld`). No money, no outcome, any
--                         number per attempt. The attempt stays PENDING.
--   kind = 'ask'       -- a RE-ASK of a pending attempt was sent, under the SAME
--                         attempt id as its idempotency key and for the amount
--                         and currency RESERVED. No money, no outcome, any
--                         number per attempt. Written and committed before the
--                         processor is asked again, as the reservation was.
--   kind = 'outcome'   -- what the processor said, as a row naming the attempt:
--                         success, decline or error. On success the card
--                         payment is written in the SAME transaction. At most
--                         one per attempt.
--   kind = 'late'      -- what the processor said to an ask that was at the
--                         processor when the OPERATOR resolved the attempt: the
--                         answer landed after the outcome row and is recorded
--                         beside it, never dropped. Carries the outcome and the
--                         detail (and the reference, when given), no money. A
--                         late SUCCESS the operator did not record writes the
--                         card payment for the amount reserved in the same
--                         transaction -- the money moved. It is not an outcome
--                         row: the fold does not count it, and the attempt is
--                         not pending.
--   kind = 'payment_method_changed' -- as before, now written under the invoice
--                         lock so that its place in the log is its place in
--                         time relative to the reservations around it.
--
-- Per attempt the log reads `attempt, [unknown, ask, unknown, ask ...], outcome`
-- -- and, when the operator resolved it while a re-ask was at the processor,
-- `late` after the outcome.
-- A pending attempt -- an `attempt` row with no `outcome` row -- is never
-- charged past: if its last row is `attempt` or `ask` a request may be in
-- flight and the next charge is refused by name; if its last row is `unknown`
-- the next charge writes an `ask` row and asks the processor again under the
-- same key. What the module does not know is never written as an outcome. The
-- log stays append-only by grant; nothing here is updated.
--
-- `sequence` is the log's RECORDED order -- an identity the database assigns
-- at insert, under the invoice lock -- and is what the retry fold and the
-- pending-attempt read order by. `occurred_at` is the caller's instant (an
-- operator's typed `--at`, a worker's clock) and decides nothing about order.
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
  ADD COLUMN currency     char(3),
  ADD COLUMN sequence     bigint GENERATED ALWAYS AS IDENTITY UNIQUE;

ALTER TABLE charge_attempts
  DROP CONSTRAINT charge_attempts_kind_check,
  DROP CONSTRAINT charge_attempts_outcome_iff_attempt;

ALTER TABLE charge_attempts
  ADD CONSTRAINT charge_attempts_kind_check
    CHECK (kind IN ('attempt', 'unknown', 'ask', 'outcome', 'late', 'payment_method_changed')),
  -- The shapes, each all-or-nothing. A reservation names its attempt and
  -- carries the money and no outcome; an unknown and an ask name their attempt
  -- and carry neither money nor outcome; an outcome, and a late answer, names
  -- its attempt, says how it went, and carries no money of its own (the
  -- reservation already did); a method change carries none of it.
  ADD CONSTRAINT charge_attempts_shape_by_kind CHECK (
    (kind = 'attempt'
       AND outcome IS NULL AND attempt_id IS NOT NULL
       AND amount_minor IS NOT NULL AND amount_minor > 0 AND currency IS NOT NULL) OR
    (kind IN ('unknown', 'ask')
       AND outcome IS NULL AND attempt_id IS NOT NULL
       AND amount_minor IS NULL AND currency IS NULL) OR
    (kind IN ('outcome', 'late')
       AND outcome IS NOT NULL AND attempt_id IS NOT NULL
       AND amount_minor IS NULL AND currency IS NULL) OR
    (kind = 'payment_method_changed'
       AND outcome IS NULL AND attempt_id IS NULL
       AND amount_minor IS NULL AND currency IS NULL)
  );

-- One reservation and at most one outcome per attempt id; unknowns, asks and
-- late answers are any number and are not indexed here. The module refuses a second outcome
-- by name under the invoice lock before this fires, so through the module's
-- own paths the index is never reached. What it does when it IS reached -- a
-- raw insert, or the check bypassed in-process -- is hand the module a
-- violation it catches BY THIS NAME and turns into the same refusal
-- (REFUSAL_ATTEMPT_ALREADY_RESOLVED; see charging.ONE_OUTCOME_PER_ATTEMPT), as
-- the one-reversal-per-payment key and the one-agreement-per-garage key are
-- caught. It does not decide which of two resolvers wins: the lock does.
CREATE UNIQUE INDEX charge_attempts_one_row_per_kind_per_attempt
  ON charge_attempts (tenant_id, attempt_id, kind)
  WHERE attempt_id IS NOT NULL AND kind IN ('attempt', 'outcome');

-- The fold and the pending read walk one invoice's log in recorded order.
CREATE INDEX charge_attempts_invoice_sequence_idx
  ON charge_attempts (tenant_id, invoice_id, sequence);

-- ---------------------------------------------------------------------------
-- Every garage reference is half of a COMPOSITE TENANT KEY.
--
-- 0001 gave `agreements` and `invoices` a bare `garage_id REFERENCES
-- garages(id)`. The outside pass's second round inserted, as the application
-- role inside tenant A, a row naming tenant B's garage uuid: accepted, on both
-- 0001 tables and on this migration's first cut of `vehicle_registrations`.
-- Inert through the module (tenant A reads by its own garage uuid, tenant B's
-- policy hides the row) and a hole all the same, for the reason 0002 gave when
-- it gave every money-history row a composite key: a foreign-key check runs
-- past row-level security and the policy alone does not stop a pointer across
-- tenants. So `garages` gains the pair every garage reference points at, as
-- `invoices` did in 0002, and the two 0001 tables gain the composite key
-- beside the bare one they keep (0001 is not edited). A guarantee reads the
-- catalogue: every `garage_id` column in the schema is half of such a key.
-- ---------------------------------------------------------------------------
ALTER TABLE garages ADD CONSTRAINT garages_tenant_id_id_key UNIQUE (tenant_id, id);

ALTER TABLE agreements
  ADD CONSTRAINT agreements_garage_in_tenant
  FOREIGN KEY (tenant_id, garage_id)
  REFERENCES garages (tenant_id, id) ON DELETE RESTRICT;

ALTER TABLE invoices
  ADD CONSTRAINT invoices_garage_in_tenant
  FOREIGN KEY (tenant_id, garage_id)
  REFERENCES garages (tenant_id, id) ON DELETE RESTRICT;

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
-- The module refuses BEFORE it writes: every listed identity is checked and
-- the first one another agreement holds raises, and only then does the release
-- DELETE and the inserts run -- so a refusal leaves the caller's transaction
-- exactly as it found it. Two registrations racing end in this table's UNIQUE
-- aborting the second's transaction; the module names that by constraint, and
-- the caller rolls back.
--
-- Current state, not money: G12 covers this table from the catalogue (tenant
-- column, ENABLE, FORCE, policy). Its garage reference is a COMPOSITE tenant
-- key, `(tenant_id, garage_id) REFERENCES garages (tenant_id, id)`, for the
-- reason 0002 gave for every money-history row: a foreign-key check runs past
-- row-level security, so a bare `garage_id REFERENCES garages(id)` lets a
-- tenant-A row name tenant B's garage by a raw insert, and a composite key does
-- not. The same key is added below to 0001's `agreements` and `invoices`,
-- whose bare references were the same hole.
-- ---------------------------------------------------------------------------
CREATE TABLE vehicle_registrations (
  id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  garage_id              uuid        NOT NULL,
  identity_normalised    text        NOT NULL CHECK (length(btrim(identity_normalised)) > 0),
  agreement_external_id  text        NOT NULL CHECK (length(btrim(agreement_external_id)) > 0),
  registered_at          timestamptz NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT vehicle_registrations_one_agreement_per_garage
    UNIQUE (tenant_id, garage_id, identity_normalised),
  CONSTRAINT vehicle_registrations_garage_in_tenant
    FOREIGN KEY (tenant_id, garage_id)
    REFERENCES garages (tenant_id, id) ON DELETE CASCADE
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
