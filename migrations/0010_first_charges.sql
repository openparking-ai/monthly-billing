-- 0010 — the first charge is a stored invoice, and the periods it owns are
-- stored with it, per agreement: one invoice per payer and sign-up day,
-- collecting for every spot that payer signed up that day.
--
-- Run as the database OWNER, after 0009. The application never connects as
-- this role. 0001–0009 are not edited. A new table only: no existing row is
-- read or changed, so this is safe on a populated store.
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
-- THE INVOICE'S DAY. `invoices.period_start_day` is half of the one-invoice-
-- per-payer-per-period lock (0002), and the run keys its invoices on period
-- start days. A first charge is keyed on the agreement's start day, or on the
-- day before it when the agreement starts ON a period's first day -- a day
-- that is never a period's first day, so a first charge never takes the key
-- of the payer's own period invoice.
--
-- APPEND-ONLY BY GRANT: SELECT and INSERT.

BEGIN;

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
