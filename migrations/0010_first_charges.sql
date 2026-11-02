-- 0010 — the first charge is a stored invoice, and the periods it owns are
-- stored with it, per agreement: one invoice per payer and sign-up day,
-- collecting for every spot that payer signed up that day.
--
-- Run as the database OWNER, after 0009. The application never connects as
-- this role. 0001–0009 are not edited. A new table, a new column with a
-- constant default (no rewrite), and the period lock widened by that column:
-- no existing row is changed, and the widened lock admits every row the old
-- one did, so this is safe on a populated store. Rebuilding the lock's index
-- holds a lock on `invoices` for the length of the build.
--
-- WHY. The first charge -- the period containing the agreement's start day
-- and the one after it -- was computed from documents and stored nowhere, and
-- the billing run skips both periods for that agreement. So the store held no
-- invoice for them: covered-in-store found nothing unpaid and answered COVERED
-- for a parker who never paid, and nothing could record the payment. And the
-- run decided which periods the first charge owned by recomputing them from
-- the LATEST version's start day, so a later version that moved the start
-- day moved the owned periods and the run billed a period the first charge
-- had already been paid for.
--
-- Now `issue-first-charge` writes the first charge as an ordinary invoice --
-- through the run's own path: priced, taxed once at its due instant, the
-- invoice's one-per-payer-per-day lock, paid derived -- and, in the same
-- transaction, one row here per agreement on it, naming the agreement, the
-- invoice and the two periods it owns. The run reads the owned periods from
-- this row. One first charge per agreement, by constraint; several agreements
-- -- several spots one payer signed up on one day -- may share one invoice.
--
-- THE FIRST CHARGE HAS ITS OWN KEY. `invoices.period_start_day` is half of
-- the one-invoice-per-payer-per-period lock (0002), and the run keys its
-- period invoices on period start days. A first charge is keyed on its
-- sign-up day, so it would share a key with the payer's period invoice when
-- the sign-up day is a billing day, and the day-before workaround made two
-- first charges of one payer on consecutive days (the second on a billing
-- day) share one. So invoices gain `first_charge` (false on every existing
-- row) and the lock becomes one invoice per payer, per period, PER KIND:
-- period invoices are still one per payer per period, first charges are one
-- per payer per sign-up day, and the two never collide. The lock keeps its
-- name, so the run's ALREADY_ISSUED reading of it is unchanged. Widening a
-- UNIQUE cannot fail on rows the narrower one already admitted. The column is
-- set at issue and never changes: 0008 grants UPDATE on `paid_at` alone.
--
-- APPEND-ONLY BY GRANT: SELECT and INSERT.

BEGIN;

ALTER TABLE invoices ADD COLUMN first_charge boolean NOT NULL DEFAULT false;

ALTER TABLE invoices DROP CONSTRAINT invoices_one_per_payer_per_period;
ALTER TABLE invoices
  ADD CONSTRAINT invoices_one_per_payer_per_period
  UNIQUE (tenant_id, garage_id, payer_id, period_start_day, first_charge);

CREATE TABLE first_charges (
  id                       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  garage_id                uuid        NOT NULL,
  agreement_external_id    text        NOT NULL CHECK (length(btrim(agreement_external_id)) > 0),
  agreement_id             uuid        NOT NULL,
  invoice_id               uuid        NOT NULL,
  first_period_start_day   date        NOT NULL,
  second_period_start_day  date        NOT NULL,
  second_period_end_day    date        NOT NULL,
  created_at               timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT first_charges_one_per_agreement
    UNIQUE (tenant_id, garage_id, agreement_external_id),
  CONSTRAINT first_charges_one_per_version UNIQUE (tenant_id, agreement_id),
  CONSTRAINT first_charges_periods_in_order CHECK (
    first_period_start_day < second_period_start_day
    AND second_period_start_day < second_period_end_day
  ),
  CONSTRAINT first_charges_garage_in_tenant
    FOREIGN KEY (tenant_id, garage_id) REFERENCES garages (tenant_id, id) ON DELETE RESTRICT,
  CONSTRAINT first_charges_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE RESTRICT,
  CONSTRAINT first_charges_invoice_in_tenant
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX first_charges_tenant_id_idx ON first_charges (tenant_id);
CREATE INDEX first_charges_invoice_idx   ON first_charges (tenant_id, invoice_id);
ALTER TABLE first_charges ENABLE ROW LEVEL SECURITY;
ALTER TABLE first_charges FORCE  ROW LEVEL SECURITY;
CREATE POLICY first_charges_tenant_isolation ON first_charges
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

GRANT SELECT, INSERT ON first_charges TO monthly_billing_app;

COMMIT;
