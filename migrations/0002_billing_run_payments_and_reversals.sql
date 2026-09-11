-- 0002 — the billing run's lock, payments received, reversals, and the charge
-- log that replaces the retry counter.
--
-- Run as the database OWNER, after 0001. The application never connects as
-- this role.
--
-- THREE TABLES HERE ARE APPEND-ONLY, AND THAT IS DONE WITH GRANTS. Money
-- history is never edited: a payment that bounced is not deleted, it gets a
-- reversal row beside it; a charge attempt that failed is not overwritten, the
-- next one is a new row. The application role is granted SELECT and INSERT on
-- these three and nothing else, so an UPDATE or a DELETE is refused by the
-- database whatever the application meant. The public precedent for this shape
-- is the events table of a sibling repository in this project, which grants
-- exactly SELECT, INSERT and says why.

BEGIN;

-- ---------------------------------------------------------------------------
-- invoices — one per payer, per garage, per period, BY CONSTRAINT.
--
-- The billing run is idempotent because the database refuses a second invoice
-- for the same period, not because the run remembered it had already issued
-- one. A second run of the same period hits this, reports it per payer, and
-- issues nothing. The existing UNIQUE (tenant_id, reference) is the second lock
-- on the same fact, since the reference is derived from the same three things.
-- ---------------------------------------------------------------------------
ALTER TABLE invoices
  ADD CONSTRAINT invoices_one_per_payer_per_period
  UNIQUE (tenant_id, garage_id, payer_id, period_start_day);

-- The pair every money-history row points at. Redundant beside the primary key
-- on purpose: a composite foreign key (tenant_id, invoice_id) needs a unique
-- target of the same shape, and that key is what stops a payment in one tenant
-- from pointing at an invoice in another -- a foreign-key check runs past
-- row-level security, so the policy alone would not.
ALTER TABLE invoices ADD CONSTRAINT invoices_tenant_id_id_key UNIQUE (tenant_id, id);

-- ---------------------------------------------------------------------------
-- owner_exceptions -- an amount is a positive number of minor units. The kind
-- carries the direction; the sign never does. A negative credit read as a debit
-- is exactly the quiet inversion this refuses, and zero is not an amount.
-- ---------------------------------------------------------------------------
ALTER TABLE owner_exceptions
  ADD CONSTRAINT owner_exceptions_amount_is_positive CHECK (amount_minor > 0);

-- ---------------------------------------------------------------------------
-- payments — money received against an invoice. Card, cheque or ACH.
--
-- ⛔ THERE IS NO COLUMN HERE THAT COULD HOLD A CARD NUMBER OR A BANK ACCOUNT
-- NUMBER, AND THERE NEVER WILL BE. `processor_reference` is the processor's
-- reference for a card outcome, the cheque number, or the ACH trace -- text the
-- application refuses an instrument-shaped value in, on the way in, as it does
-- for every text column. A card brand and the last four digits MAY be stored on
-- a card payment: four digits are not a card number. Nothing longer may, and
-- the CHECK below is the structural half of that.
--
-- `paid` is not a column. An invoice is paid when the sum of its unreversed
-- payments reaches its total, and `invoices.paid_at` is DERIVED from that by the
-- application after every payment, reversal and adjustment. See payments.py.
-- ---------------------------------------------------------------------------
CREATE TABLE payments (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  invoice_id           uuid        NOT NULL,
  method               text        NOT NULL CHECK (method IN ('card', 'cheque', 'ach')),
  amount_minor         bigint      NOT NULL CHECK (amount_minor > 0),
  currency             char(3)     NOT NULL,
  received_at          timestamptz NOT NULL,
  processor_reference  text,
  card_brand           text,
  card_last4           char(4)     CHECK (card_last4 ~ '^[0-9]{4}$'),
  recorded_by          text        NOT NULL CHECK (length(btrim(recorded_by)) > 0),
  created_at           timestamptz NOT NULL DEFAULT now(),

  -- The card columns exist for a card payment and for nothing else. A cheque
  -- with a card brand on it is a record somebody assembled wrong.
  CONSTRAINT payments_card_fields_only_on_a_card CHECK (
    method = 'card' OR (card_brand IS NULL AND card_last4 IS NULL)
  ),
  -- The invoice this pays is in THIS tenant, by key and not by policy.
  CONSTRAINT payments_invoice_in_tenant
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE RESTRICT,
  UNIQUE (tenant_id, id)
);

CREATE INDEX payments_tenant_id_idx ON payments (tenant_id);
CREATE INDEX payments_invoice_idx   ON payments (tenant_id, invoice_id);
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments FORCE  ROW LEVEL SECURITY;
CREATE POLICY payments_tenant_isolation ON payments
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- payment_reversals — a payment that did not stand. A second row, never an
-- edit to the first.
--
-- What happens NEXT -- a fee, a block, a credit -- is the owner's decision and
-- is recorded as an owner exception. This table records the fact that the money
-- did not arrive, and nothing else. One reversal per payment: a payment that
-- did not stand cannot un-stand twice.
-- ---------------------------------------------------------------------------
CREATE TABLE payment_reversals (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  payment_id   uuid        NOT NULL,
  reason       text        NOT NULL
               CHECK (reason IN ('bounced_cheque', 'ach_returned', 'chargeback',
                                 'processor_reversed')),
  reversed_at  timestamptz NOT NULL,
  recorded_by  text        NOT NULL CHECK (length(btrim(recorded_by)) > 0),
  note         text        NOT NULL DEFAULT '',
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT payment_reversals_payment_in_tenant
    FOREIGN KEY (tenant_id, payment_id) REFERENCES payments (tenant_id, id) ON DELETE RESTRICT,
  UNIQUE (tenant_id, payment_id)
);

CREATE INDEX payment_reversals_tenant_id_idx ON payment_reversals (tenant_id);
ALTER TABLE payment_reversals ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_reversals FORCE  ROW LEVEL SECURITY;
CREATE POLICY payment_reversals_tenant_isolation ON payment_reversals
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- charge_attempts — REPLACED. It was a counter: one row per invoice, an
-- `attempts` column, the last outcome, and UPDATE granted. A counter that is
-- reset overwrites what happened, and this estate keeps money history
-- append-only. It is now a log: one row per event, and the retry state is
-- REBUILT by reading the rows since the last `payment_method_changed`.
--
-- NO DATA MIGRATION, AND THAT IS ASSERTED RATHER THAN ASSUMED. This module has
-- never been deployed, so there are no rows to carry -- but a migration that
-- silently dropped a populated table would be exactly the kind of quiet loss
-- this project catalogues, so it refuses to run if any row exists.
--
-- `detail` is what the processor said, which makes it the column an instrument
-- arrives in by accident; the application refuses one on the way in.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  populated integer;
BEGIN
  SELECT count(*) INTO populated FROM charge_attempts;
  IF populated > 0 THEN
    RAISE EXCEPTION
      'charge_attempts holds % row(s). This migration replaces the table and carries '
      'no data, because the module had never been deployed when it was written. '
      'That premise no longer holds here: stop, and write the data migration.',
      populated;
  END IF;
END
$$;

DROP TABLE charge_attempts;

CREATE TABLE charge_attempts (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  invoice_id   uuid        NOT NULL,
  kind         text        NOT NULL CHECK (kind IN ('attempt', 'payment_method_changed')),
  outcome      text        CHECK (outcome IN ('success', 'decline', 'error')),
  detail       text        NOT NULL DEFAULT '',
  occurred_at  timestamptz NOT NULL,
  recorded_by  text        NOT NULL CHECK (length(btrim(recorded_by)) > 0),
  created_at   timestamptz NOT NULL DEFAULT now(),

  -- An attempt says how it went; a method change has nothing to say about an
  -- outcome. Either shape without the other half is a row assembled wrong.
  CONSTRAINT charge_attempts_outcome_iff_attempt CHECK (
    (kind =  'attempt' AND outcome IS NOT NULL) OR
    (kind <> 'attempt' AND outcome IS NULL)
  ),
  CONSTRAINT charge_attempts_invoice_in_tenant
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX charge_attempts_tenant_id_idx ON charge_attempts (tenant_id);
CREATE INDEX charge_attempts_invoice_idx   ON charge_attempts (tenant_id, invoice_id, occurred_at);
ALTER TABLE charge_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE charge_attempts FORCE  ROW LEVEL SECURITY;
CREATE POLICY charge_attempts_tenant_isolation ON charge_attempts
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- Grants. SELECT and INSERT only on the three append-only tables -- no UPDATE,
-- no DELETE -- so a row, once written, stays written. The old charge_attempts
-- grant died with the old table.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT ON payments, payment_reversals, charge_attempts TO monthly_billing_app;

-- And the invoice's own money is not edited either. 0001 granted the application
-- DML on everything; a priced line and a recorded decision are history the same
-- way a payment is, so UPDATE and DELETE come back off invoice_lines and
-- owner_exceptions, and DELETE off invoices. UPDATE on invoices STAYS: paid_at is
-- derived by the application after every payment, reversal and adjustment, and
-- that is the one column it writes. Nothing in the module updates or deletes
-- any of these; the revoke makes the grant say what the code already does.
REVOKE UPDATE, DELETE ON invoice_lines, owner_exceptions FROM monthly_billing_app;
REVOKE DELETE ON invoices FROM monthly_billing_app;

COMMIT;
