-- 0006 — a garage's tax sets, the tax position an invoice was priced at, and
-- tax lines on an invoice.
--
-- Run as the database OWNER, after 0005. The application never connects as
-- this role. 0001–0005 are not edited: what they created stands, and this
-- migration alters it.
--
-- THIS SCHEMA COMPUTES NO PERCENTAGE, AND NEITHER DOES THE MODULE. The tax on
-- a monthly invoice is `rate-engine`'s `tax.py` -- the same computation the
-- barrier's tax is -- called in-process through the `tax` extra (see
-- src/monthly_billing/tax.py). These tables store what the engine accepted and
-- what it answered. Nothing here multiplies anything.
--
-- THESE TABLES JUDGE NO TAX SET. Whether a set is valid is said by the
-- engine's `load_tax_sets` before the store writes it, and nowhere else: a
-- second validator here would be a second opinion free to disagree with the
-- one that prices. What is left is storage: the types, the tenant, the garage,
-- and one set per instant.
--
-- THREE STATES AND NO FOURTH, the owner's activation rule: a garage states the
-- taxes it charges (a set with rules), or states that it charges none (a set
-- with NO rules -- a statement, written), or has stated nothing (no set in
-- force). The third is a REFUSAL to invoice, by name, never a zero. Stating
-- nothing is not stating none.

BEGIN;

-- ---------------------------------------------------------------------------
-- garage_tax_sets — a garage's taxes, as SETS, each in force from an instant.
--
-- The shape is the engine's: `effective_from` and `rules`, each rule exactly
-- `id`, `label`, `percent_bp`, `rounding`, `sequence`. A set is ONE ROW, its
-- rules held as the engine echoed them, so a set can never arrive half-written
-- and need no count to prove it did not (platform's 0022 keeps rules in a
-- second table and needs a deferred trigger for exactly that).
--
-- APPEND-ONLY BY GRANT. A rate changing, a tax added and a tax repealed are
-- all a new set with a later `effective_from`. A set is never edited and never
-- deleted, because every invoice names the set that priced it and every
-- staleness argument downstream depends on that set still being there to read.
--
-- TWO SETS AT ONE INSTANT IS A REFUSAL. The engine refuses them at load; the
-- UNIQUE is the one place both of two RACING statements are seen, since each
-- would load alone. `timestamptz` compares instants, so `10:00-05:00` and
-- `15:00Z` are one value here as they are to the engine.
--
-- `stated_by` is who stated it: a tax position decides money, and who changed
-- a garage's money is an audit fact. No personal data beyond that name.
--
-- The garage reference is a COMPOSITE TENANT KEY, for the reason 0003 gave
-- for every garage reference: a foreign-key check runs past row-level
-- security, and a bare key would let a tenant-A row name tenant B's garage.
-- ---------------------------------------------------------------------------
CREATE TABLE garage_tax_sets (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  garage_id       uuid        NOT NULL,
  effective_from  timestamptz NOT NULL,
  rules           jsonb       NOT NULL CHECK (jsonb_typeof(rules) = 'array'),
  stated_by       text        NOT NULL CHECK (length(btrim(stated_by)) > 0),
  created_at      timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT garage_tax_sets_one_set_per_instant
    UNIQUE (tenant_id, garage_id, effective_from),
  CONSTRAINT garage_tax_sets_garage_in_tenant
    FOREIGN KEY (tenant_id, garage_id)
    REFERENCES garages (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX garage_tax_sets_tenant_id_idx ON garage_tax_sets (tenant_id);
ALTER TABLE garage_tax_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE garage_tax_sets FORCE  ROW LEVEL SECURITY;
CREATE POLICY garage_tax_sets_tenant_isolation ON garage_tax_sets
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

GRANT SELECT, INSERT ON garage_tax_sets TO monthly_billing_app;

-- ---------------------------------------------------------------------------
-- invoices — the tax position each one was priced at.
--
--   subtotal_minor          the pre-tax total AT ISSUE. A historical fact, like
--                           a line's agreement_version: an adjustment later
--                           moves the invoice's current pre-tax total, which is
--                           DERIVED from its non-tax lines whenever it is
--                           needed, and nothing stores a second copy of it.
--   tax_set_effective_from  which set priced it -- a key into garage_tax_sets,
--                           so it names a set that exists, in this tenant, at
--                           this garage.
--   tax_sets_seen           how many sets the garage held in the transaction
--                           that priced it. With the column above, the
--                           staleness instrument the lane uses: a backdated
--                           set stated later changes the count even when it
--                           does not change the newest instant.
--
-- NULL on every invoice issued before this migration, all three together, and
-- that is their honest value: they were issued untaxed. Nothing backfills a
-- guess. From this migration the run writes all three on every invoice.
--
-- ALL THREE ARE IMMUTABLE. 0002 keeps UPDATE on `invoices` for `paid_at`, and
-- 0003 says that grant must not be tightened (the invoice lock needs it), so a
-- column-level revoke is not available. A BEFORE UPDATE trigger refuses any
-- change to any of the three and names the one that was touched.
-- ---------------------------------------------------------------------------
ALTER TABLE invoices
  ADD COLUMN subtotal_minor          bigint      CHECK (subtotal_minor >= 0),
  ADD COLUMN tax_set_effective_from  timestamptz,
  ADD COLUMN tax_sets_seen           integer     CHECK (tax_sets_seen >= 1);

ALTER TABLE invoices
  ADD CONSTRAINT invoices_tax_position_is_whole CHECK (
    (subtotal_minor IS NULL AND tax_set_effective_from IS NULL AND tax_sets_seen IS NULL) OR
    (subtotal_minor IS NOT NULL AND tax_set_effective_from IS NOT NULL
       AND tax_sets_seen IS NOT NULL)
  ),
  ADD CONSTRAINT invoices_tax_set_in_tenant
    FOREIGN KEY (tenant_id, garage_id, tax_set_effective_from)
    REFERENCES garage_tax_sets (tenant_id, garage_id, effective_from) ON DELETE RESTRICT;

CREATE FUNCTION invoices_tax_position_is_immutable() RETURNS trigger
  LANGUAGE plpgsql
  AS $$
BEGIN
  IF NEW.subtotal_minor IS DISTINCT FROM OLD.subtotal_minor THEN
    RAISE EXCEPTION 'invoices.subtotal_minor is immutable: it is the pre-tax total at issue'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'invoices_tax_position_is_immutable';
  END IF;
  IF NEW.tax_set_effective_from IS DISTINCT FROM OLD.tax_set_effective_from THEN
    RAISE EXCEPTION 'invoices.tax_set_effective_from is immutable: it names the set that priced the invoice'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'invoices_tax_position_is_immutable';
  END IF;
  IF NEW.tax_sets_seen IS DISTINCT FROM OLD.tax_sets_seen THEN
    RAISE EXCEPTION 'invoices.tax_sets_seen is immutable: it is what the pricing transaction saw'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'invoices_tax_position_is_immutable';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER invoices_tax_position_is_immutable
  BEFORE UPDATE ON invoices
  FOR EACH ROW EXECUTE FUNCTION invoices_tax_position_is_immutable();

-- ---------------------------------------------------------------------------
-- invoice_lines — a `tax` line.
--
-- A TAX LINE BELONGS TO THE INVOICE, NOT TO ONE AGREEMENT UNDER IT. Its base
-- is the whole invoice's non-tax total, so stamping one agreement onto it
-- would be false data on a money row. `agreement_id` and `agreement_version`
-- become nullable, and a CHECK makes them NULL exactly on a tax line and
-- present on every other kind -- so no other line loses its agreement.
--
-- A tax line names the RULE that produced it (`tax_rule_id`, the engine's
-- rule `id`), and nothing else does. That is what lets a later adjustment
-- re-tax rule by rule: the compensating line for a rule is the engine's tax
-- for that rule on the new base, minus what that rule's lines already carry.
--
-- An adjustment line names its exception, as 0001 required. A tax line MAY
-- name one too: the compensating tax an exception's adjustment caused names
-- that exception, and a tax line written at issue names none. Every other
-- kind still names none.
-- ---------------------------------------------------------------------------
ALTER TABLE invoice_lines
  DROP CONSTRAINT invoice_lines_kind_check,
  DROP CONSTRAINT invoice_lines_adjustments_name_their_exception;

ALTER TABLE invoice_lines
  ALTER COLUMN agreement_id      DROP NOT NULL,
  ALTER COLUMN agreement_version DROP NOT NULL,
  ADD COLUMN tax_rule_id text;

ALTER TABLE invoice_lines
  ADD CONSTRAINT invoice_lines_kind_check
    CHECK (kind IN ('partial_period', 'full_period', 'recurring_fee',
                    'one_time_fee', 'exception_adjustment', 'tax')),
  ADD CONSTRAINT invoice_lines_adjustments_name_their_exception CHECK (
    (kind = 'exception_adjustment' AND exception_id IS NOT NULL) OR
    (kind = 'tax') OR
    (kind NOT IN ('exception_adjustment', 'tax') AND exception_id IS NULL)
  ),
  ADD CONSTRAINT invoice_lines_tax_names_no_agreement CHECK (
    (kind = 'tax' AND agreement_id IS NULL AND agreement_version IS NULL) OR
    (kind <> 'tax' AND agreement_id IS NOT NULL AND agreement_version IS NOT NULL)
  ),
  ADD CONSTRAINT invoice_lines_tax_names_its_rule CHECK (
    (kind = 'tax' AND tax_rule_id IS NOT NULL AND length(btrim(tax_rule_id)) > 0) OR
    (kind <> 'tax' AND tax_rule_id IS NULL)
  );

-- ---------------------------------------------------------------------------
-- THE SUBTOTAL IS THE ISSUE-TIME SUM OF THE NON-TAX LINES, CHECKED AT COMMIT.
--
-- Not a CHECK: the money is spread over `invoice_lines` and the invoice row is
-- written first, so no single row can see both. A deferred constraint trigger
-- on the invoice runs at the END of the issuing transaction, when every line
-- the issue wrote is there, and holds `subtotal_minor` to the sum of the
-- non-tax lines written IN THAT TRANSACTION -- the rows sharing the invoice
-- row's `xmin`. Lines an adjustment adds later are a different transaction and
-- are not the issue-time figure.
--
-- `subtotal + tax = total` is NOT a constraint, deliberately: the total is
-- derived from the lines, so once this one holds, that one is an identity and
-- would prove nothing.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoices_subtotal_is_the_issue_lines() RETURNS trigger
  LANGUAGE plpgsql
  AS $$
DECLARE
  issued_in xid;
  issued    bigint;
BEGIN
  SELECT i.xmin INTO issued_in FROM invoices i WHERE i.id = NEW.id;
  SELECT coalesce(sum(l.amount_minor), 0) INTO issued
    FROM invoice_lines l
   WHERE l.invoice_id = NEW.id AND l.kind <> 'tax' AND l.xmin = issued_in;
  IF issued <> NEW.subtotal_minor THEN
    RAISE EXCEPTION 'invoice %: subtotal_minor is % and the non-tax lines it was issued with sum to %',
      NEW.reference, NEW.subtotal_minor, issued
      USING ERRCODE = 'check_violation', CONSTRAINT = 'invoices_subtotal_is_the_issue_lines';
  END IF;
  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER invoices_subtotal_is_the_issue_lines
  AFTER INSERT ON invoices
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (NEW.subtotal_minor IS NOT NULL)
  EXECUTE FUNCTION invoices_subtotal_is_the_issue_lines();

-- ---------------------------------------------------------------------------
-- AN ADJUSTMENT ON A TAXED INVOICE IS RE-TAXED IN ITS OWN TRANSACTION.
--
-- Tax is the last COMPUTATION on an invoice, not the last ROW: lines stay
-- append-only, so an owner's waiver or credit lands after the tax lines, and
-- the tax must move with the base it was computed on. The module's one writer
-- of adjustments appends the adjustment AND, for every rule of the set the
-- invoice recorded, the compensating tax line the engine's answer implies --
-- one transaction.
--
-- This trigger is the structural half: at COMMIT, an `exception_adjustment`
-- line on an invoice whose recorded tax set HAS RULES is refused unless a
-- `tax` line naming the same exception was written in the same transaction
-- (same `xmin`). "Nothing follows tax" could not be enforced; this can.
--
-- WHAT IT CANNOT HOLD, STATED: that the compensating line is the RIGHT amount.
-- That needs the percentage, which this schema does not compute, so it is a
-- guarantee with a control in the test suite and not a constraint here.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoice_lines_adjustment_is_retaxed() RETURNS trigger
  LANGUAGE plpgsql
  AS $$
DECLARE
  written_in xid;
  rule_count integer;
BEGIN
  SELECT jsonb_array_length(s.rules) INTO rule_count
    FROM invoices i
    JOIN garage_tax_sets s
      ON s.tenant_id = i.tenant_id AND s.garage_id = i.garage_id
     AND s.effective_from = i.tax_set_effective_from
   WHERE i.id = NEW.invoice_id;
  IF coalesce(rule_count, 0) = 0 THEN
    RETURN NULL;  -- untaxed (issued before 0006), or a set that states no tax
  END IF;
  SELECT l.xmin INTO written_in FROM invoice_lines l WHERE l.id = NEW.id;
  IF NOT EXISTS (
    SELECT 1 FROM invoice_lines t
     WHERE t.invoice_id = NEW.invoice_id AND t.kind = 'tax'
       AND t.exception_id = NEW.exception_id AND t.xmin = written_in
  ) THEN
    RAISE EXCEPTION 'adjustment % on a taxed invoice was written without its compensating tax line',
      NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'invoice_lines_adjustment_is_retaxed';
  END IF;
  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER invoice_lines_adjustment_is_retaxed
  AFTER INSERT ON invoice_lines
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (NEW.kind = 'exception_adjustment')
  EXECUTE FUNCTION invoice_lines_adjustment_is_retaxed();

COMMIT;
